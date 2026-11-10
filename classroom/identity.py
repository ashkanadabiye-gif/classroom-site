def get_identity(session, user, room):
    """هویت بازدیدکننده: کاربر واردشده یا مهمان (با نام ذخیره‌شده در نشست). در غیر این صورت None."""
    if user.is_authenticated:
        return {
            "key": f"u{user.pk}", "user_id": user.pk, "guest": False,
            "name": user.get_full_name() or user.username,
            "teacher": room.owner_id == user.pk,
        }
    sid = session.get("student_id")
    if sid:
        from .models import Student
        st = Student.objects.filter(pk=sid, owner_id=room.owner_id).first()
        if st:
            return {"key": f"s{st.pk}", "user_id": None, "guest": False, "name": st.full_name, "teacher": False}
    g = session.get("guest")
    if g and room.allow_guests and room.pk in session.get("guest_rooms", []):
        return {"key": f"g{g['id']}", "user_id": None, "guest": True, "name": g["name"], "teacher": False}
    return None
