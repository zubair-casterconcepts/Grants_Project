# Generated for customizable chat starter prompts.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_conversation_message'),
    ]

    operations = [
        migrations.CreateModel(
            name='StarterPrompt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.SlugField(help_text='Stable identifier, e.g. find_grants.', max_length=64, unique=True)),
                ('title', models.CharField(help_text='Card heading shown to the user.', max_length=120)),
                ('description', models.CharField(blank=True, help_text='Card subtitle shown under the heading.', max_length=200)),
                ('action', models.CharField(choices=[('search', 'Search grants'), ('update_project', 'Update project'), ('link', 'Open link'), ('focus_input', 'Focus composer')], default='search', help_text='What clicking the card does.', max_length=32)),
                ('query', models.CharField(blank=True, help_text='SEARCH cards only: exact text passed to the matcher with the saved profile.', max_length=500)),
                ('href', models.CharField(blank=True, help_text='LINK cards only: URL to open (e.g. /accounts/saved/).', max_length=300)),
                ('position', models.PositiveIntegerField(default=0, help_text='Sort order (ascending).')),
                ('is_active', models.BooleanField(default=True, help_text='Uncheck to hide the card.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Starter prompt',
                'verbose_name_plural': 'Starter prompts',
                'db_table': 'grant_starter_prompt',
                'ordering': ['position', 'id'],
            },
        ),
    ]
