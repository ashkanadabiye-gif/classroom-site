import re
import time
import uuid
from collections import deque
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from .identity import get_identity
from .models import ActiveParticipant, Attendance, ChatMessage, Poll, PrivateMessage, Room, RoomBan, Vote

# دسترسی‌های دانش‌آموز (استاد همیشه همه را دارد)
DEFAULT_PERMS = {"mic": False, "cam": False, "board": False, "screen": False, "chat": True}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
OP_KINDS = {"path", "highlight", "erase", "line", "rect", "ellipse", "triangle", "arrow", "text"}
BOARD_MSGS = {"board_op", "board_pts", "board_undo", "board_clear", "board_sync", "board_page", "laser"}


def clean_perms(d):
    if not isinstance(d, dict):
        return {}
    return {k: bool(v) for k, v in d.items() if k in DEFAULT_PERMS}


def num(v, lo, hi, default=0.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def clean_pts(pts, limit):
    out = []
    for p in (pts if isinstance(pts, list) else [])[:limit]:
        try:
            out.append([round(num(p[0], 0, 1), 4), round(num(p[1], 0, 1), 4)])
        except (TypeError, IndexError, KeyError):
            continue
    return out


def clean_op(op, prefix):
    if not isinstance(op, dict) or op.get("kind") not in OP_KINDS:
        return None
    oid = str(op.get("id", ""))[:40]
    pts = clean_pts(op.get("pts"), 5000)
    if not oid.startswith(prefix) or not pts:
        return None
    color = str(op.get("color", ""))
    out = {"id": oid, "kind": op["kind"], "pts": pts, "size": num(op.get("size"), 1, 60, 3),
           "color": color if COLOR_RE.match(color) else "#111111"}
    if op["kind"] == "text":
        out["text"] = str(op.get("text", ""))[:200]
    return out


class RoomConsumer(AsyncJsonWebsocketConsumer):
    """سیگنالینگ WebRTC + چت + تخته + مدیریت دسترسی‌ها و اخراج."""

    async def connect(self):
        kw = self.scope["url_route"]["kwargs"]
        res = await self.setup(kw["owner"], kw["slug"])
        if res is None:
            await self.close()
            return
        self.room, self.ident, defaults = res
        self.peer_id = uuid.uuid4().hex[:12]
        self.is_teacher = self.ident["teacher"]
        self.name = self.ident["name"]
        self.perms = {k: True for k in DEFAULT_PERMS} if self.is_teacher else {**DEFAULT_PERMS, **defaults}
        self.group = f"room_{self.room.pk}"
        self._chat_events = deque()
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.register_participant()
        files, poll, my_vote = await self.welcome_data()
        self.att_id = await self.att_start()
        await self.send_json({"type": "welcome", "peer_id": self.peer_id, "perms": self.perms,
                              "history": await self.history(), "private_history": await self.private_history(),
                              "active_students": await self.active_students(),
                              "files": files, "poll": poll, "my_vote": my_vote})
        await self.broadcast({"type": "join"})

    async def disconnect(self, code):
        if hasattr(self, "att_id"):
            await self.att_end()
        if hasattr(self, "peer_id"):
            await self.unregister_participant()
        if hasattr(self, "group"):
            await self.broadcast({"type": "leave"})
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, c):
        if not isinstance(c, dict):
            return
        kind = c.get("type")
        t = self.is_teacher
        if kind in {"chat", "private_chat"}:
            now = time.monotonic()
            while self._chat_events and now - self._chat_events[0] > 5:
                self._chat_events.popleft()
            if len(self._chat_events) >= 8:
                await self.send_json({"type": "rate_limited", "message": "لطفاً کمی آهسته‌تر پیام بفرستید."})
                return
            self._chat_events.append(now)
        if kind == "signal" and c.get("to"):
            await self.broadcast({"type": "signal", "to": c["to"], "data": c.get("data")})
        elif kind == "chat" and self.perms["chat"]:
            text = str(c.get("text", "")).strip()[:1000]
            if text:
                await self.save_message(text)
                await self.broadcast({"type": "chat", "text": text, "echo": True,
                                      "t": timezone.localtime().strftime("%H:%M")})
        elif kind == "private_chat" and self.perms["chat"] and c.get("to"):
            text = str(c.get("text", "")).strip()[:1000]
            target = str(c.get("to", ""))[:32]
            if text and target:
                private = await self.create_private_message(target, text)
                if private:
                    for peer_id in private["peer_ids"]:
                        await self.broadcast({"type": "private_chat", "to": peer_id,
                                              "text": text, "t": timezone.localtime().strftime("%H:%M"),
                                              "private_sender": self.name,
                                              "conversation_key": private["conversation_key"],
                                              "sender_peer_id": self.peer_id})
        elif kind == "rename" and self.ident.get("guest"):
            new_name = " ".join(str(c.get("name", "")).split())[:40]
            if len(new_name) >= 2:
                self.name = new_name
                self.ident["name"] = new_name
                await self.broadcast({"type": "rename", "name": new_name, "echo": True})
        elif kind == "hand":
            await self.broadcast({"type": "hand", "up": bool(c.get("up"))})
        elif kind == "perm" and t and c.get("to"):
            await self.broadcast({"type": "perm", "to": c["to"], "perms": clean_perms(c.get("perms"))})
        elif kind == "perm_all" and t:
            perms = clean_perms(c.get("perms"))
            await self.save_defaults(perms)
            await self.broadcast({"type": "perm", "perms": perms})
        elif kind == "kick" and t and c.get("to"):
            await self.broadcast({"type": "kick", "to": c["to"]})
        elif kind == "focus" and not t:  # دانش‌آموز از تب کلاس خارج/برگشت (فقط استاد می‌بیند)
            await self.broadcast({"type": "focus", "away": bool(c.get("away")), "only_teacher": True})
        elif kind == "poll_create" and t:
            poll = await self.poll_create(c)
            if poll:
                await self.broadcast({"type": "poll_open", "poll": poll, "echo": True})
        elif kind == "poll_vote" and not t:
            counts = await self.poll_vote(c.get("id"), c.get("choice"))
            if counts is not None:
                await self.send_json({"type": "poll_ack", "id": c["id"], "choice": c["choice"]})
                await self.broadcast({"type": "poll_counts", "id": c["id"], "counts": counts, "only_teacher": True})
        elif kind == "poll_close" and t:
            res = await self.poll_close(c.get("id"))
            if res:
                await self.broadcast({"type": "poll_closed", "echo": True, **res})
        elif kind in BOARD_MSGS:
            await self.handle_board(kind, c)

    async def handle_board(self, kind, c):
        t = self.is_teacher
        if kind in ("board_clear", "board_sync", "board_page"):
            if not t:
                return
        elif not (t or self.perms["board"]):
            return
        prefix = self.peer_id + "-"
        payload = None
        if kind == "board_op":
            op = clean_op(c.get("op"), prefix)
            payload = {"type": kind, "op": op} if op else None
        elif kind == "board_pts":
            oid = str(c.get("id", ""))[:40]
            pts = clean_pts(c.get("pts"), 500)
            payload = {"type": kind, "id": oid, "pts": pts} if oid.startswith(prefix) and pts else None
        elif kind == "board_undo":
            oid = str(c.get("id", ""))[:40]
            payload = {"type": kind, "id": oid} if (t or oid.startswith(prefix)) else None
        elif kind == "board_clear":
            payload = {"type": kind}
        elif kind == "board_sync":
            ops = [o for o in (clean_op(o, "") for o in (c.get("ops") or [])[:6000]) if o]
            payload = {"type": kind, "to": c.get("to"), "ops": ops, "page": int(num(c.get("page", 0), 0, 99, 0))}
        elif kind == "board_page":
            ops = [o for o in (clean_op(o, "") for o in (c.get("ops") or [])[:6000]) if o]
            payload = {"type": kind, "ops": ops, "page": int(num(c.get("page", 0), 0, 99, 0))}
        elif kind == "laser":
            pts = clean_pts([c.get("pt")], 1)
            payload = {"type": kind, "pt": pts[0]} if pts else None
        if payload:
            await self.broadcast(payload)

    async def broadcast(self, payload):
        payload.update(sender=self.peer_id, name=self.name, teacher=self.is_teacher,
                       guest=self.ident["guest"], sender_perms=self.perms)
        await self.channel_layer.group_send(self.group, {"type": "relay", "payload": payload})

    async def relay(self, event):
        p = event["payload"]
        if p.get("sender") == getattr(self, "peer_id", None) and not p.get("echo"):
            return
        if p.get("to") and p["to"] != self.peer_id:
            return
        if p.get("only_teacher") and not self.is_teacher:
            return
        # فرمان‌های استاد (فیلد teacher را سرور می‌گذارد و از کلاینت قابل جعل نیست)
        if p["type"] == "perm" and p.get("teacher") and not self.is_teacher:
            self.perms.update(clean_perms(p.get("perms")))
            await self.send_json({"type": "perms", "perms": self.perms})
            await self.broadcast({"type": "perm_state"})
            return
        if p["type"] == "kick" and p.get("teacher") and not self.is_teacher:
            await self.send_json({"type": "kicked"})
            await self.block_me()
            await self.close()
            return
        await self.send_json(p)

    # ---------- دیتابیس ----------
    @database_sync_to_async
    def setup(self, owner, slug):
        room = Room.objects.select_related("owner").filter(owner__username=owner, slug=slug).first()
        if room is None or not room.is_active:
            return None
        ident = get_identity(self.scope["session"], self.scope["user"], room)
        if ident is None:
            return None
        if RoomBan.objects.filter(room=room, key=ident["key"], until__gt=timezone.now()).exists():
            return None
        return room, ident, clean_perms(room.default_perms)

    @database_sync_to_async
    def register_participant(self):
        ActiveParticipant.objects.update_or_create(
            peer_id=self.peer_id,
            defaults={"room": self.room, "identity_key": self.ident["key"],
                      "display_name": self.name, "is_teacher": self.is_teacher})

    @database_sync_to_async
    def unregister_participant(self):
        ActiveParticipant.objects.filter(peer_id=self.peer_id, room=self.room).delete()

    @database_sync_to_async
    def create_private_message(self, target, text):
        """دانش‌آموز فقط به استاد پیام می‌دهد؛ استاد فقط به دانش‌آموز فعال همین کلاس پاسخ می‌دهد."""
        if self.is_teacher:
            recipient = ActiveParticipant.objects.filter(
                room=self.room, peer_id=target, is_teacher=False).first()
            if recipient is None:
                return None
            recipient_key = recipient.identity_key
            recipient_name = recipient.display_name
            peer_ids = [recipient.peer_id]
            conversation_key = recipient_key
        else:
            if target != "teacher":
                return None
            recipient_key = f"u{self.room.owner_id}"
            recipient_name = self.room.owner.get_full_name() or self.room.owner.username
            peer_ids = list(ActiveParticipant.objects.filter(
                room=self.room, is_teacher=True).values_list("peer_id", flat=True))
            conversation_key = self.ident["key"]

        PrivateMessage.objects.create(
            room=self.room, sender_key=self.ident["key"], sender_name=self.name,
            recipient_key=recipient_key, recipient_name=recipient_name, text=text)
        return {"peer_ids": peer_ids, "conversation_key": conversation_key}

    @database_sync_to_async
    def private_history(self):
        qs = PrivateMessage.objects.filter(room=self.room)
        if not self.is_teacher:
            qs = qs.filter(Q(sender_key=self.ident["key"]) | Q(recipient_key=self.ident["key"]))
        rows = list(qs.order_by("-id")[:300])
        out = []
        for m in reversed(rows):
            key = m.sender_key if self.is_teacher and m.sender_key != f"u{self.room.owner_id}" else m.recipient_key
            if not self.is_teacher:
                key = self.ident["key"]
            out.append({"conversation_key": key, "sender_key": m.sender_key,
                        "sender_name": m.sender_name, "recipient_key": m.recipient_key,
                        "recipient_name": m.recipient_name,
                        "from_teacher": m.sender_key == f"u{self.room.owner_id}",
                        "text": m.text, "t": timezone.localtime(m.created_at).strftime("%H:%M")})
        return out

    @database_sync_to_async
    def active_students(self):
        if not self.is_teacher:
            return []
        return list(ActiveParticipant.objects.filter(room=self.room, is_teacher=False)
                    .values("peer_id", "identity_key", "display_name").order_by("display_name"))

    @database_sync_to_async
    def save_defaults(self, perms):
        r = Room.objects.get(pk=self.room.pk)
        r.default_perms = {**clean_perms(r.default_perms), **perms}
        r.save(update_fields=["default_perms"])

    @database_sync_to_async
    def block_me(self):
        RoomBan.objects.update_or_create(
            room=self.room, key=self.ident["key"],
            defaults={"until": timezone.now() + timedelta(minutes=settings.KICK_BLOCK_MINUTES)})

    @database_sync_to_async
    def save_message(self, text):
        ChatMessage.objects.create(room=self.room, user_id=self.ident["user_id"],
                                   guest_name="" if self.ident["user_id"] else self.name, text=text)

    @database_sync_to_async
    def history(self):
        qs = ChatMessage.objects.filter(room=self.room).select_related("user").order_by("-id")[:50]
        out = []
        for m in reversed(list(qs)):
            name = (m.user.get_full_name() or m.user.username) if m.user else m.guest_name
            out.append({"name": name, "text": m.text, "t": timezone.localtime(m.created_at).strftime("%H:%M")})
        return out

    # ---------- حضور و غیاب ----------
    @database_sync_to_async
    def att_start(self):
        return Attendance.objects.create(room=self.room, key=self.ident["key"], name=self.name,
                                         joined_at=timezone.now()).pk

    @database_sync_to_async
    def att_end(self):
        Attendance.objects.filter(pk=self.att_id).update(left_at=timezone.now())

    # ---------- فایل‌ها و نظرسنجی ----------
    @staticmethod
    def _counts(poll):
        n = len(poll.options)
        counts = [0] * n
        for v in poll.votes.values_list("choice", flat=True):
            if 0 <= v < n:
                counts[v] += 1
        return counts

    @database_sync_to_async
    def welcome_data(self):
        files = [{"id": f.pk, "name": f.name, "size": f.size} for f in self.room.files.order_by("-id")[:50]]
        poll = my_vote = None
        p = self.room.polls.filter(is_open=True).order_by("-id").first()
        if p:
            poll = {"id": p.pk, "q": p.question, "options": p.options, "quiz": p.correct is not None}
            if self.is_teacher:
                poll["counts"] = self._counts(p)
            v = Vote.objects.filter(poll=p, key=self.ident["key"]).first()
            my_vote = v.choice if v else None
        return files, poll, my_vote

    @database_sync_to_async
    def poll_create(self, c):
        q = str(c.get("q", "")).strip()[:300]
        opts = [str(o).strip()[:100] for o in (c.get("options") or []) if str(o).strip()][:6]
        if not q or len(opts) < 2:
            return None
        corr = c.get("correct")
        corr = corr if isinstance(corr, int) and 0 <= corr < len(opts) else None
        Poll.objects.filter(room=self.room, is_open=True).update(is_open=False)
        p = Poll.objects.create(room=self.room, question=q, options=opts, correct=corr)
        return {"id": p.pk, "q": q, "options": opts, "quiz": corr is not None}

    @database_sync_to_async
    def poll_vote(self, pid, choice):
        p = Poll.objects.filter(pk=pid, room=self.room, is_open=True).first() if isinstance(pid, int) else None
        if not p or not isinstance(choice, int) or not 0 <= choice < len(p.options):
            return None
        _, created = Vote.objects.get_or_create(poll=p, key=self.ident["key"], defaults={"choice": choice})
        return self._counts(p) if created else None

    @database_sync_to_async
    def poll_close(self, pid):
        p = Poll.objects.filter(pk=pid, room=self.room, is_open=True).first() if isinstance(pid, int) else None
        if not p:
            return None
        p.is_open = False
        p.save(update_fields=["is_open"])
        return {"id": p.pk, "counts": self._counts(p), "correct": p.correct}
