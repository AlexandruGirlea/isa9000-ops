from django.db import migrations


def name_groups_after_their_id(apps, schema_editor):
    EntraGroup = apps.get_model("app_portal", "EntraGroup")
    groups = list(EntraGroup.objects.filter(name=""))
    for group in groups:
        group.name = str(group.pk)
    EntraGroup.objects.bulk_update(groups, ["name"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('app_portal', '0002_library_access'),
    ]

    operations = [
        migrations.RunPython(name_groups_after_their_id, migrations.RunPython.noop),
    ]
