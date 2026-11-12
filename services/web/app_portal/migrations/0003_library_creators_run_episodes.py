from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def creators_keep_their_libraries(apps, schema_editor):
    # A creator could create in every library they could access. Keep exactly that on upgrade.
    Profile = apps.get_model("app_portal", "Profile")
    LibraryGrant = apps.get_model("app_portal", "LibraryGrant")
    Creator = Profile.creator_libraries.through
    members = Profile.groups.through.objects.filter(
        profile__role="creator", profile__membership="complete", entragroup__status="enabled",
    ).values_list("profile_id", "entragroup_id")
    grants = LibraryGrant.objects.filter(
        Q(expires__isnull=True) | Q(expires__gt=timezone.now()),
        profile__role="creator", revoked__isnull=True, library__status="enabled",
    ).values_list("profile_id", "library_id")
    Creator.objects.bulk_create(
        [
            Creator(profile_id=profile, entragroup_id=library)
            for profile, library in sorted({*members, *grants})
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app_portal', '0002_library_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='creator_libraries',
            field=models.ManyToManyField(blank=True, help_text='This person can create and manage podcasts in these libraries. They also need access to each one: Entra membership or local library access.', related_name='creators', to='app_portal.entragroup', verbose_name='creator in libraries'),
        ),
        migrations.AddField(
            model_name='run',
            name='episodes',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='entragroup',
            name='status',
            field=models.CharField(choices=[('hidden', 'Hidden'), ('enabled', 'Enabled')], db_default='hidden', db_index=True, default='hidden', help_text='<b>Hidden:</b> nobody can use it in the portal, including administrators. Groups appear here this way automatically when a member signs in. Hiding a library stops its pages, audio and shared podcasts. All data is kept, and setting Enabled again restores it.<br><b>Enabled:</b> a library. Its creators and administrators see it straight away and can add podcasts to it. Other members see it once it has a published podcast.', max_length=10),
        ),
        migrations.RunPython(creators_keep_their_libraries, migrations.RunPython.noop),
    ]
