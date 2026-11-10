import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.urls import reverse


def make_slug():
    return secrets.token_urlsafe(6)


class Room(models.Model):
    title = models.CharField("عنوان کلاس", max_length=200)
    description = models.TextField("توضیحات", blank=True)
    slug = models.CharField("آدرس کلاس", max_length=40, default=make_slug)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rooms")
    scheduled_at = models.DateTimeField("زمان برگزاری", null=True, blank=True)
    allow_guests = models.BooleanField("اجازه‌ی ورود مهمان (فقط با وارد کردن نام)", default=True)
    default_perms = models.JSONField(default=dict, blank=True, editable=False)  # دسترسی پیش‌فرض دانش‌آموزان
    is_active = models.BooleanField("فعال", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("owner", "slug")

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("room", args=[self.owner.username, self.slug])


class ChatMessage(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    guest_name = models.CharField(max_length=60, blank=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class RoomBan(models.Model):
    """ممنوعیت موقت ورود بعد از اخراج (کلید: u<id> برای کاربر، g<id> برای مهمان)."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="bans")
    key = models.CharField(max_length=40)
    until = models.DateTimeField()

    class Meta:
        unique_together = ("room", "key")


class Student(models.Model):
    """دانش‌آموزی که استاد از قبل تعریف کرده (نام، نام کاربری، رمز). در همه‌ی کلاس‌های همان استاد معتبر است."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="students")
    full_name = models.CharField("نام و نام خانوادگی", max_length=100)
    username = models.CharField("نام کاربری", max_length=40)
    password = models.CharField(max_length=128)  # فقط هش ذخیره می‌شود
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        unique_together = ("owner", "username")

    def __str__(self):
        return f"{self.full_name} ({self.username})"

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return check_password(raw, self.password)


class SharedFile(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="files")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to="rooms/%Y/%m/")
    size = models.BigIntegerField(default=0)
    uploader = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Poll(models.Model):
    """نظرسنجی (بدون پاسخ درست) یا آزمون سریع (با گزینه‌ی درست)."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="polls")
    question = models.CharField(max_length=300)
    options = models.JSONField(default=list)
    correct = models.IntegerField(null=True, blank=True)
    is_open = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Vote(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes")
    key = models.CharField(max_length=40)
    choice = models.SmallIntegerField()

    class Meta:
        unique_together = ("poll", "key")


class Attendance(models.Model):
    """هر بار اتصال یک نفر به کلاس (برای گزارش حضور و غیاب)."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="attendance")
    key = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    joined_at = models.DateTimeField()
    left_at = models.DateTimeField(null=True, blank=True)


class ClassRecording(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="recordings")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="recordings/%Y/%m/")
    size = models.BigIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    uploader = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ActiveParticipant(models.Model):
    """اتصال فعال برای مسیریابی امن پیام خصوصی در همان کلاس."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="active_participants")
    peer_id = models.CharField(max_length=32, unique=True)
    identity_key = models.CharField(max_length=80)
    display_name = models.CharField(max_length=100)
    is_teacher = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["room", "identity_key"], name="classroom_a_room_id_7bd27f_idx")]


class PrivateMessage(models.Model):
    """پیام خصوصیِ یک‌به‌یک میان دبیر و یکی از شرکت‌کنندگان کلاس."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="private_messages")
    sender_key = models.CharField(max_length=80)
    sender_name = models.CharField(max_length=100)
    recipient_key = models.CharField(max_length=80)
    recipient_name = models.CharField(max_length=100)
    text = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["room", "sender_key"], name="classroom_p_room_id_34a1d4_idx"), models.Index(fields=["room", "recipient_key"], name="classroom_p_room_id_ef4c12_idx")]
