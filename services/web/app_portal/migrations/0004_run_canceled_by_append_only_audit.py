import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

APPEND_ONLY = """
CREATE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'The audit log cannot be changed or deleted.';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON django_admin_log
FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
"""
REMOVE_APPEND_ONLY = """
DROP TRIGGER audit_log_append_only ON django_admin_log;
DROP FUNCTION audit_log_is_append_only();
"""


def protect_audit_log(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(APPEND_ONLY)


def unprotect_audit_log(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REMOVE_APPEND_ONLY)


class Migration(migrations.Migration):

    dependencies = [
        ('admin', '0003_logentry_add_action_flag_choices'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app_portal', '0003_group_id_as_default_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='run',
            name='canceled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='run',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('running', 'Picked up by a worker'), ('ready', 'Ready'), ('needs_review', 'Needs review'), ('failed', 'Failed'), ('stopped', 'User canceled')], default='queued', max_length=12),
        ),
        migrations.RunPython(protect_audit_log, unprotect_audit_log),
    ]
