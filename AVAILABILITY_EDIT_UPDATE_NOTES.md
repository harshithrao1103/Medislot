# Availability edit and future-slot reconciliation

## Changes

- Fixed the Weekly Availability Edit button by replacing fragile inline JavaScript (whose quoted Jinja values could break HTML attributes) with data attributes and event listeners.
- Editing fills consultation type, weekday, start/end times and the hidden availability ID. Saving uses the existing PUT endpoint and updates the same RDS row.
- Successful dashboard creation, editing and deletion queues the existing slot-materialiser Lambda asynchronously from the EC2 dashboard. The existing daily scheduled materialisation remains as a fallback.
- Added least-privilege `lambda:InvokeFunction` to the dashboard EC2 instance role in `Security_IAM_Stack.yaml`, and configures the function name and AWS region both at first boot and on existing EC2 deployments via SSM.
- Materialiser preserves stable IDs for matching slots, creates missing future slots, and marks obsolete slots `INACTIVE` when there is no active hold. Already-booked appointments and counters remain stored, but no additional booking is allowed on obsolete slots. Version increments on status changes.
- The materialiser avoids touching elapsed slots when reconciling a window containing the current IST date.

## Important boundaries

- Slot duration is defined by the consultation type, **not globally 30 minutes**. In this project Quick Consult is 15 minutes and Standard Consult is 30 minutes. The 9:00–9:30 / 9:30–10:00 example applies to Standard Consult.
- Existing active holds are left untouched by reconciliation until hold expiry. A later materialiser run must mark them inactive if they remain obsolete.
- Asynchronous Lambda invocation (HTTP 202) confirms acceptance, not successful completion. Verify CloudWatch logs and DynamoDB for a real deployment. The dashboard will display a warning if the invocation could not be queued.
- Direct API calls that bypass the Flask dashboard do not trigger immediate materialisation; they rely on the daily scheduler. A future event-driven backend implementation would cover all API clients.
- Live AWS integration, IAM deployment and browser testing have not been performed in this offline environment.

## Deployment

1. Commit and push the updated CloudFormation, dashboard, workflow, Lambda and test files.
2. Run the existing `deploy.yaml` workflow (security IAM stack runs before application stack).
3. Confirm `Update EC2 Doctor Dashboard` succeeded and the service is running.
4. Test Edit and Save on a 30-minute Standard Consult availability; inspect RDS and DynamoDB and CloudWatch logs.
5. Avoid testing with real patient bookings until HTTPS and production safeguards are in place.
