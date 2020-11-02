# Generated by Django 3.1.2 on 2020-11-02 12:43

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Cluster',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.SlugField(max_length=32, unique=True)),
                ('plan_name', models.SlugField(max_length=32)),
                ('uuid', models.UUIDField()),
                ('last_action', models.CharField(max_length=100)),
                ('last_action_state', models.CharField(max_length=100)),
                ('last_action_description', models.CharField(max_length=200)),
                ('kubernetes_master_host', models.CharField(max_length=200)),
                ('kubernetes_master_port', models.IntegerField()),
                ('pks_version', models.CharField(blank=True, max_length=30)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
