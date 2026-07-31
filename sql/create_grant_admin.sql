-- Run in Supabase SQL Editor AFTER Django migrations have created grant_user / grant_profile.
-- Login credentials:
--   username: admin
--   password: Admin@12345
-- Change the password after first login.

INSERT INTO grant_user (
    password,
    last_login,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
) VALUES (
    'pbkdf2_sha256$1200000$tU9rEbDfP0mZPWZu7xfoKA$cN5EAvCJFNyjsyBSXdr0vsx+L+RFArIRsloLW9JHPCU=',
    NULL,
    TRUE,
    'admin',
    '',
    '',
    'admin@grants.local',
    TRUE,
    TRUE,
    NOW()
)
ON CONFLICT (username) DO NOTHING;

INSERT INTO grant_profile (organization, role_title, created_at, updated_at, user_id)
SELECT '', '', NOW(), NOW(), id
FROM grant_user
WHERE username = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM grant_profile gp WHERE gp.user_id = grant_user.id
  );
