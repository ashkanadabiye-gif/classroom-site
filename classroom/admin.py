from django.contrib import admin

from .models import ActiveParticipant, Attendance, ChatMessage, Poll, PrivateMessage, Room, RoomBan, SharedFile, Student, Vote

admin.site.register(Room)
admin.site.register(ChatMessage)
admin.site.register(RoomBan)
admin.site.register(Student)
for m in (SharedFile, Poll, Vote, Attendance, ActiveParticipant, PrivateMessage):
    admin.site.register(m)
