import csv
import os
import re
import secrets
import uuid
from urllib.parse import quote

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import RoomForm
from .identity import get_identity
from .models import ClassRecording, Room, RoomBan, SharedFile, Student
from .utils import norm_name


def home(request):
    return render(request, "home.html")


def register(request):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form, "next": nxt})


@login_required
def dashboard(request):
    return render(request, "dashboard.html", {"my_rooms": Room.objects.filter(owner=request.user)})


@login_required
def room_create(request):
    form = RoomForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        room = form.save(commit=False)
        room.owner = request.user
        room.save()
        return redirect("dashboard")
    return render(request, "room_form.html", {"form": form})


SESSION_SECONDS = 6 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def random_password(n=6):
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(n))


def _join_page(request, room):
    """صفحه‌ی ورود با لینک کلاس: «مهمان» (فقط نام) یا «ورود» (نام + نام کاربری + رمز عبور)."""
    ctx = {"room": room, "error": None, "full_name": "", "username": ""}
    if not room.is_active:
        return render(request, "room_join.html", {**ctx, "error": "این کلاس پایان یافته است.", "ended": True})
    if request.method == "POST":
        action = request.POST.get("action")
        full_name = " ".join(request.POST.get("full_name", "").split())[:100]
        username = request.POST.get("username", "").strip()[:150]
        password = request.POST.get("password", "")
        ctx.update(full_name=full_name, username=username)
        if action == "guest" and room.allow_guests:
            if len(full_name) < 2:
                ctx["error"] = "لطفاً نام خود را وارد کنید (حداقل ۲ حرف)."
            else:
                g = request.session.get("guest") or {"id": uuid.uuid4().hex}
                g["name"] = full_name[:40]
                request.session["guest"] = g
                rooms = request.session.get("guest_rooms", [])
                if room.pk not in rooms:
                    rooms.append(room.pk)
                request.session["guest_rooms"] = rooms
                request.session.set_expiry(SESSION_SECONDS)
                return redirect(room)
        elif action == "login":
            fkey = f"joinfail:{room.pk}:{request.META.get('REMOTE_ADDR', '')}:{username.lower()}"
            if cache.get(fkey, 0) >= 8:
                ctx["error"] = "تلاش‌های ناموفق زیاد بود؛ ۱۰ دقیقه بعد دوباره تلاش کنید."
            else:
                # ۱) دانش‌آموز ثبت‌شده‌ی این استاد: نام + نام کاربری + رمز باید با هم مطابقت کنند
                st = Student.objects.filter(owner=room.owner, username__iexact=username).first() if username else None
                if st and st.check_password(password) and norm_name(st.full_name) == norm_name(full_name):
                    request.session["student_id"] = st.pk
                    request.session.set_expiry(SESSION_SECONDS)
                    cache.delete(fkey)
                    return redirect(room)
                # ۲) حساب Django (استاد یا کاربر ثبت‌نام‌شده): نام کاربری + رمز
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    login(request, user)
                    cache.delete(fkey)
                    return redirect(room)
                cache.set(fkey, cache.get(fkey, 0) + 1, 600)
                ctx["error"] = "نام، نام کاربری و رمز عبور با اطلاعات ثبت‌شده مطابقت ندارد."
    return render(request, "room_join.html", ctx)


def room_detail(request, owner, slug):
    room = get_object_or_404(Room.objects.select_related("owner"), owner__username=owner, slug=slug)
    ident = get_identity(request.session, request.user, room)
    if ident is None:
        return _join_page(request, room)
    if not room.is_active:
        if ident:
            return render(request, "room_recordings.html", {"room": room, "recordings": room.recordings.all()})
        return render(request, "room_join.html", {"room": room, "error": "این کلاس پایان یافته است.", "ended": True})
    if RoomBan.objects.filter(room=room, key=ident["key"], until__gt=timezone.now()).exists():
        return render(request, "room_join.html", {
            "room": room, "ended": True,
            "error": f"شما توسط استاد از این کلاس خارج شده‌اید. بعد از {settings.KICK_BLOCK_MINUTES} دقیقه دوباره تلاش کنید."})
    return render(request, "room.html", {
        "room": room,
        "base": room.get_absolute_url(),
        "ws_path": f"/ws/ch/{quote(room.owner.username, safe='')}/{quote(room.slug, safe='')}/",
        "is_teacher": ident["teacher"],
        "is_guest": ident["guest"],
        "display_name": ident["name"],
        "ice_servers": settings.ICE_SERVERS,
        "recordings": [{"id": r.pk, "title": r.title, "size": r.size,
                        "created": timezone.localtime(r.created_at).strftime("%Y/%m/%d %H:%M"),
                        "url": reverse("room_recording_download", args=[room.owner.username, room.slug, r.pk])}
                       for r in room.recordings.all()[:100]],
        "record_upload_url": reverse("room_recording_upload", args=[room.owner.username, room.slug]),
        "rename_url": reverse("room_rename", args=[room.owner.username, room.slug]),
        "skyroom_reference_images": [f"/static/classroom/skyroom_reference/{i}.jpg" for i in range(1, 11)],
    })


@login_required
@require_POST
def room_end(request, owner, slug):
    room = get_object_or_404(Room, owner=request.user, owner__username=owner, slug=slug)
    room.is_active = False
    room.save(update_fields=["is_active"])
    async_to_sync(get_channel_layer().group_send)(
        f"room_{room.pk}",
        {"type": "relay", "payload": {"type": "ended", "sender": "server"}},
    )
    return redirect("dashboard")


@require_POST
def room_leave(request, owner, slug):
    """خروج از کلاس؛ برای دانش‌آموز/مهمان هویت ذخیره‌شده‌ی این مرورگر را پاک می‌کند (مفید روی کامپیوتر مشترک)."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    room = get_object_or_404(Room, owner__username=owner, slug=slug)
    request.session.pop("student_id", None)
    request.session["guest_rooms"] = [r for r in request.session.get("guest_rooms", []) if r != room.pk]
    return redirect(room)


def _add_student(owner, full_name, username, password):
    full_name = " ".join(full_name.split())[:100]
    username, password = username.strip(), password.strip()
    if len(full_name) < 2:
        return None, "نام و نام خانوادگی را وارد کنید."
    if not USERNAME_RE.match(username):
        return None, f"نام کاربری «{username}» معتبر نیست (لاتین، ۳ تا ۴۰ نویسه: حروف، عدد، . _ -)."
    if Student.objects.filter(owner=owner, username__iexact=username).exists():
        return None, f"نام کاربری «{username}» تکراری است."
    if password and len(password) < 4:
        return None, "رمز عبور حداقل ۴ نویسه باشد."
    password = password or random_password()
    st = Student(owner=owner, full_name=full_name, username=username)
    st.set_password(password)
    st.save()
    return (st.full_name, st.username, password), None


@login_required
def students(request):
    """مدیریت دانش‌آموزان: افزودن تکی/گروهی، رمز جدید، حذف. رمزهای ساخته‌شده فقط همین‌جا یک‌بار نمایش داده می‌شوند."""
    creds, errors = [], []
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "add":
            res, err = _add_student(request.user, request.POST.get("full_name", ""),
                                    request.POST.get("username", ""), request.POST.get("password", ""))
            creds.append(res) if res else errors.append(err)
        elif act == "bulk":
            for n, line in enumerate(request.POST.get("bulk", "").splitlines(), 1):
                if not line.strip():
                    continue
                parts = (re.split(r"[,،\t]", line, maxsplit=2) + ["", ""])[:3]
                res, err = _add_student(request.user, *parts)
                creds.append(res) if res else errors.append(f"خط {n}: {err}")
        elif act in ("reset", "delete"):
            st = get_object_or_404(Student, pk=request.POST.get("id"), owner=request.user)
            if act == "delete":
                st.delete()
                return redirect("students")
            pw = request.POST.get("password", "").strip() or random_password()
            if len(pw) < 4:
                errors.append("رمز عبور حداقل ۴ نویسه باشد.")
            else:
                st.set_password(pw)
                st.save()
                creds.append((st.full_name, st.username, pw))
    return render(request, "students.html", {
        "students": Student.objects.filter(owner=request.user), "creds": creds, "errors": errors})


# ---------- فایل‌های کلاس ----------
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".msi", ".ps1", ".sh", ".vbs", ".scr", ".com", ".jar", ".dll", ".apk"}


def _notify(room, payload):
    async_to_sync(get_channel_layer().group_send)(
        f"room_{room.pk}", {"type": "relay", "payload": {**payload, "sender": "server"}})


def _room_and_ident(request, owner, slug):
    room = get_object_or_404(Room.objects.select_related("owner"), owner__username=owner, slug=slug)
    return room, get_identity(request.session, request.user, room)


@require_POST
def room_file_upload(request, owner, slug):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident or not ident["teacher"]:
        return JsonResponse({"error": "فقط استاد می‌تواند فایل بارگذاری کند."}, status=403)
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "فایلی انتخاب نشده است."}, status=400)
    if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return JsonResponse({"error": f"حجم فایل بیشتر از {settings.MAX_UPLOAD_MB} مگابایت است."}, status=413)
    name = os.path.basename(f.name)[:200]
    if os.path.splitext(name)[1].lower() in BLOCKED_EXT:
        return JsonResponse({"error": "این نوع فایل مجاز نیست."}, status=400)
    sf = SharedFile.objects.create(room=room, name=name, file=f, size=f.size, uploader=ident["name"])
    _notify(room, {"type": "file_added", "file": {"id": sf.pk, "name": sf.name, "size": sf.size}})
    return JsonResponse({"ok": True})


def room_file_download(request, owner, slug, fid):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident:
        raise Http404
    sf = get_object_or_404(SharedFile, pk=fid, room=room)
    return FileResponse(sf.file.open("rb"), as_attachment=True, filename=sf.name)


@require_POST
def room_file_delete(request, owner, slug, fid):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident or not ident["teacher"]:
        return JsonResponse({"error": "forbidden"}, status=403)
    sf = get_object_or_404(SharedFile, pk=fid, room=room)
    sf.file.delete(save=False)
    sf.delete()
    _notify(room, {"type": "file_removed", "id": fid})
    return JsonResponse({"ok": True})


# ---------- گزارش حضور و غیاب / باز کردن دوباره ----------
def _fmt(sec):
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}"


@login_required
def room_report(request, owner, slug):
    room = get_object_or_404(Room, owner=request.user, owner__username=owner, slug=slug)
    now, rows = timezone.now(), {}
    kinds = {"u": "حساب کاربری", "s": "دانش‌آموز ثبت‌شده", "g": "مهمان"}
    for a in room.attendance.order_by("joined_at"):
        end = a.left_at or (now if room.is_active else a.joined_at)
        r = rows.setdefault(a.key, {"name": a.name, "kind": "استاد" if a.key == f"u{room.owner_id}" else kinds[a.key[0]],
                                    "first": a.joined_at, "sessions": 0, "sec": 0})
        r["sessions"] += 1
        r["sec"] += max(0, (end - a.joined_at).total_seconds())
        r["last"] = end
    data = sorted(rows.values(), key=lambda r: r["first"])
    for r in data:
        r["first_s"] = timezone.localtime(r["first"]).strftime("%Y/%m/%d %H:%M")
        r["last_s"] = timezone.localtime(r["last"]).strftime("%H:%M")
        r["dur"] = _fmt(r["sec"])
    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="attendance-{room.slug}.csv"'
        resp.write("\ufeff")
        w = csv.writer(resp)
        w.writerow(["نام", "نوع", "اولین ورود", "آخرین خروج", "دفعات ورود", "مدت حضور (ساعت:دقیقه)"])
        for r in data:
            w.writerow([r["name"], r["kind"], r["first_s"], r["last_s"], r["sessions"], r["dur"]])
        return resp
    return render(request, "report.html", {"room": room, "rows": data})


@login_required
@require_POST
def room_reopen(request, owner, slug):
    room = get_object_or_404(Room, owner=request.user, owner__username=owner, slug=slug)
    room.is_active = True
    room.save(update_fields=["is_active"])
    return redirect("dashboard")


def room_recordings(request, owner, slug):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident:
        return _join_page(request, room)
    return render(request, "room_recordings.html", {"room": room, "recordings": room.recordings.all()})


@require_POST
def room_recording_upload(request, owner, slug):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident or not ident["teacher"]:
        return JsonResponse({"error": "فقط استاد می‌تواند ضبط کلاس را ذخیره کند."}, status=403)
    f = request.FILES.get("recording")
    if not f:
        return JsonResponse({"error": "فایل ضبط‌شده ارسال نشده است."}, status=400)
    max_bytes = getattr(settings, "MAX_RECORDING_MB", 500) * 1024 * 1024
    if f.size > max_bytes:
        return JsonResponse({"error": "حجم فایل از محدودیت مجاز بیشتر است."}, status=413)
    ext = os.path.splitext(os.path.basename(f.name))[1].lower()
    if ext not in {".webm", ".mp4"}:
        return JsonResponse({"error": "فقط فایل ویدئویی WebM یا MP4 مجاز است."}, status=400)
    title = os.path.basename(f.name)[:200] or f"ضبط کلاس {timezone.localtime():%Y-%m-%d %H-%M}"
    try:
        duration = max(0, min(86400, int(request.POST.get("duration", 0))))
    except (TypeError, ValueError):
        duration = 0
    rec = ClassRecording.objects.create(room=room, title=title, file=f, size=f.size,
                                       duration_seconds=duration, uploader=ident["name"])
    item = {"id": rec.pk, "title": rec.title, "size": rec.size,
        "created": timezone.localtime(rec.created_at).strftime("%Y/%m/%d %H:%M"),
        "url": reverse("room_recording_download", args=[owner, slug, rec.pk])}
    _notify(room, {"type": "recording_added", "recording": item})
    return JsonResponse({"ok": True, "recording": item})


def room_recording_download(request, owner, slug, rid):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident:
        raise Http404
    rec = get_object_or_404(ClassRecording, pk=rid, room=room)
    return FileResponse(rec.file.open("rb"), as_attachment=True, filename=rec.title)


@require_POST
def room_recording_delete(request, owner, slug, rid):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident or not ident["teacher"]:
        return JsonResponse({"error": "دسترسی غیرمجاز"}, status=403)
    rec = get_object_or_404(ClassRecording, pk=rid, room=room)
    rec.file.delete(save=False)
    rec.delete()
    return JsonResponse({"ok": True})


@require_POST
def room_rename(request, owner, slug):
    room, ident = _room_and_ident(request, owner, slug)
    if not ident or not ident.get("guest"):
        return JsonResponse({"error": "تغییر نام فقط برای مهمان‌ها فعال است."}, status=403)
    name = " ".join(request.POST.get("name", "").split())[:40]
    if len(name) < 2:
        return JsonResponse({"error": "نام باید حداقل دو نویسه باشد."}, status=400)
    guest = request.session.get("guest") or {}
    guest["name"] = name
    request.session["guest"] = guest
    request.session.modified = True
    return JsonResponse({"ok": True, "name": name})
