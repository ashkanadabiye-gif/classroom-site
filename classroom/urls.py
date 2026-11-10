from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("students/", views.students, name="students"),
    path("rooms/new/", views.room_create, name="room_create"),
    path("ch/<str:owner>/<str:slug>/", views.room_detail, name="room"),
    path("ch/<str:owner>/<str:slug>/files/upload/", views.room_file_upload, name="room_file_upload"),
    path("ch/<str:owner>/<str:slug>/recordings/", views.room_recordings, name="room_recordings"),
    path("ch/<str:owner>/<str:slug>/recordings/upload/", views.room_recording_upload, name="room_recording_upload"),
    path("ch/<str:owner>/<str:slug>/recordings/<int:rid>/", views.room_recording_download, name="room_recording_download"),
    path("ch/<str:owner>/<str:slug>/recordings/<int:rid>/delete/", views.room_recording_delete, name="room_recording_delete"),
    path("ch/<str:owner>/<str:slug>/rename/", views.room_rename, name="room_rename"),
    path("ch/<str:owner>/<str:slug>/files/<int:fid>/", views.room_file_download, name="room_file"),
    path("ch/<str:owner>/<str:slug>/files/<int:fid>/delete/", views.room_file_delete, name="room_file_delete"),
    path("ch/<str:owner>/<str:slug>/report/", views.room_report, name="room_report"),
    path("ch/<str:owner>/<str:slug>/reopen/", views.room_reopen, name="room_reopen"),
    path("ch/<str:owner>/<str:slug>/leave/", views.room_leave, name="room_leave"),
    path("ch/<str:owner>/<str:slug>/end/", views.room_end, name="room_end"),
]
