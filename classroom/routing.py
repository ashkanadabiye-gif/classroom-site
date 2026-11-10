from django.urls import path

from .consumers import RoomConsumer

websocket_urlpatterns = [path("ws/ch/<str:owner>/<str:slug>/", RoomConsumer.as_asgi())]
