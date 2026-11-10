from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("classroom", "0005_attendance_poll_sharedfile_vote")]
    operations = [migrations.CreateModel(name="ClassRecording", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("title", models.CharField(max_length=200)),
        ("file", models.FileField(upload_to="recordings/%Y/%m/")),
        ("size", models.BigIntegerField(default=0)),
        ("duration_seconds", models.PositiveIntegerField(default=0)),
        ("uploader", models.CharField(blank=True, max_length=100)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recordings", to="classroom.room")),
    ], options={"ordering": ["-created_at"]})]
