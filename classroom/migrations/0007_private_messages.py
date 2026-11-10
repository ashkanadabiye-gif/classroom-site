from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("classroom", "0006_classrecording")]

    operations = [
        migrations.CreateModel(
            name="ActiveParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("peer_id", models.CharField(max_length=32, unique=True)),
                ("identity_key", models.CharField(max_length=80)),
                ("display_name", models.CharField(max_length=100)),
                ("is_teacher", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="active_participants", to="classroom.room")),
            ],
        ),
        migrations.AddIndex(model_name="activeparticipant", index=models.Index(fields=["room", "identity_key"], name="classroom_a_room_id_7bd27f_idx")),
        migrations.CreateModel(
            name="PrivateMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sender_key", models.CharField(max_length=80)),
                ("sender_name", models.CharField(max_length=100)),
                ("recipient_key", models.CharField(max_length=80)),
                ("recipient_name", models.CharField(max_length=100)),
                ("text", models.CharField(max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="private_messages", to="classroom.room")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(model_name="privatemessage", index=models.Index(fields=["room", "sender_key"], name="classroom_p_room_id_34a1d4_idx")),
        migrations.AddIndex(model_name="privatemessage", index=models.Index(fields=["room", "recipient_key"], name="classroom_p_room_id_ef4c12_idx")),
    ]
