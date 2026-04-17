---
description: Pre-flight checks before advising on CDK deploy/debug. Run this before suggesting any CDK deployment actions.
---

Run ALL of the following checks and report findings before advising on any CDK deployment action. Do NOT skip checks or assume anything.

## 1. CDK project structure
- Verify `infrastructure/cdk/cdk.json` exists and has a valid `app` entry
- Verify `infrastructure/cdk/requirements.txt` exists
- For every package in requirements.txt, verify the version constraint is satisfiable (check PyPI if unsure about latest versions)

## 2. CDK environment resolution
- Check that `app.py` resolves `account` and `region` to concrete values (not None)
- Verify AWS credentials work: suggest `aws sts get-caller-identity`
- Check if `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` are needed

## 3. Asset paths
- Find all `Code.from_asset()`, `DockerImageAsset`, and similar asset references in the CDK stacks
- Verify every referenced path exists on disk (resolve relative to the CDK directory)
- Report any missing assets

## 4. Cross-stack dependencies
- Check for security group references that cross stack boundaries (SG in StackA referenced by StackB where StackB already depends on StackA)
- Flag any potential dependency cycles

## 5. Instance types and resource constraints
- List all EC2 instance types referenced in the stacks (compute environments, launch templates, NAT instances, etc.)
- Flag any instance types that are NOT Free Tier eligible (this account has Free Tier constraints)
- Verify instance types exist in the target region

## 6. Current stack state (if deploying/debugging)
- Run: `aws cloudformation list-stacks --query 'StackSummaries[?starts_with(StackName, \`Vswe\`)].{Name:StackName,Status:StackStatus}' --output table`
- Report the state of each VSWE stack
- If any stack is in a failed/rollback state, advise on cleanup before redeploying
- NEVER assume a stack is rolling back — check the actual status

## 7. CloudFormation errors (if a deploy failed)
- Check ASG scaling activities: `aws autoscaling describe-scaling-activities --query 'Activities[0:3]' --output table`
- Check recent CF events: `aws cloudformation describe-stack-events --stack-name <failing-stack> --query 'StackEvents[0:5]' --output table`
- Report the actual error, don't guess

Report all findings as a checklist with pass/fail for each item.
