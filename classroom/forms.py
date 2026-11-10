import re

from django import forms

from .models import Room, make_slug


class RoomForm(forms.ModelForm):
    slug = forms.CharField(
        label="آدرس کلاس (لاتین؛ اگر خالی بگذارید خودکار ساخته می‌شود)", required=False, max_length=40,
        widget=forms.TextInput(attrs={"dir": "ltr", "placeholder": "مثلاً math9"}))

    class Meta:
        model = Room
        fields = ["title", "slug", "description", "scheduled_at", "allow_guests"]
        widgets = {
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.Textarea(attrs={"rows": 3}),
            "allow_guests": forms.CheckboxInput(attrs={"style": "width:auto"}),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_slug(self):
        s = self.cleaned_data["slug"].strip().lower()
        if not s:
            return make_slug()
        if not re.fullmatch(r"[a-z0-9_-]{3,40}", s):
            raise forms.ValidationError("فقط حروف لاتین، عدد، - و _ (۳ تا ۴۰ نویسه).")
        if Room.objects.filter(owner=self.owner, slug=s).exists():
            raise forms.ValidationError("شما قبلاً کلاسی با این آدرس ساخته‌اید.")
        return s
