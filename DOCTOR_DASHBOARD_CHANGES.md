# Doctor dashboard updates

This archive updates the EC2 Flask dashboard, Doctor API Lambda and CloudFormation resources.

## What changed
- Landing page offers Register and Login. Registration collects the doctor fields supported by the existing `/auth/doctors/register` API, then directs the doctor to Login. The profile loads saved MySQL details. The doctor uploads/replaces the photo after login, because the existing S3 photo-upload endpoint requires authentication.
- Access tokens refresh automatically using `/auth/refresh` (the existing non-rotating refresh token remains valid until its original expiry, configured in SSM). Sessions expire when the refresh token expires/is revoked; logout uses the existing `/auth/logout` API.
- Profile photo is displayed through a private authenticated Flask proxy and a short-lived S3 presigned GET URL. Doctor API Lambda needs `s3:GetObject` as added in `Security_IAM_Stack.yaml`.
- Consultation presets: Quick 15 min/capacity 1, Standard 30/1, Detailed 45/1, Health Camp 60/capacity 2-10. The API validates these server-side. Existing Detailed Consult records previously created at 60 minutes are not automatically migrated; edit/recreate as appropriate after reviewing existing bookings.
- Weekly availability uses a single-entry form with Add/Edit/Delete. MySQL generates `availability_id`; new entries do not replace existing ones. The original bulk PUT endpoint remains for compatibility.
- Schedule includes consultation type name, resolved from the doctor's consultation types.

## Deployment notes
1. Deploy the updated `Security_IAM_Stack.yaml` using your existing GitHub Actions/CloudFormation pipeline, before the Application stack, so the Lambda can read profile photos.
2. Package the updated `doctor_api` Lambda and `doctor_dashboard` and deploy the updated `Application_Stack.yaml` via the existing pipeline. New API Gateway methods: GET `/doctors/me/photo`, POST `/doctors/me/availability`, PUT/DELETE `/doctors/me/availability/{id}`.
3. Use a new `CodeVersion` so the API Gateway deployment and Lambda/dashboard packages update. Check the pipeline's packaging and artifact naming before deploying.
4. Verify the EC2 dashboard has a persistent writable session directory at `/var/lib/medislot/sessions`, an appropriately protected `FLASK_SECRET_KEY`, and HTTPS with `COOKIE_SECURE=true` for real credentials. The existing stack exposes HTTP by default; do not use production doctor passwords over plain HTTP.
5. Smoke test register -> login -> profile -> photo upload/display -> consultation creation -> single availability save/edit/delete -> schedule -> access-token refresh -> logout.

## Notes
- Existing appointments/slots are not migrated by this update. Availability edits take effect in the slot materialiser's next execution; check how old generated slots are handled before relying on a changed weekly pattern.
- This package was syntax/structure checked locally; live AWS, RDS, S3 and browser integration tests have **not** been executed.
