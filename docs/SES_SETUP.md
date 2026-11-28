# Amazon SES setup

1. Open Amazon SES in `us-east-1` and verify a sender email address or domain identity.
2. If the account is still in the SES sandbox, request production access or verify all recipient addresses used in testing.
3. Set `data.M6SesSenderEmail` in `Cloudformation/parameters.json` to the verified sender and redeploy the Data Stack. CloudFormation manages `/medislot/${Environment}/milestone6/ses-sender-email`; the placeholder value intentionally blocks email attempts until configured.
4. Confirm the Milestone 6 Lambda role can call `ses:SendEmail` for SES identity ARNs.
5. Confirm the SES API interface VPC endpoint is `available`; no NAT Gateway is required.
6. Trigger one test event and inspect the existing NotificationDispatch table. Expected statuses are PROCESSING, SENT, or UNKNOWN. UNKNOWN is not retried automatically.

The repository intentionally contains a placeholder sender value until the owner configures a verified identity. No sender email is hardcoded in application source.
