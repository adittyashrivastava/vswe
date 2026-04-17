#!/usr/bin/env node
/**
 * Deploy the frontend build to S3 and invalidate CloudFront.
 *
 * Usage: npm run deploy
 *
 * Since CloudFront proxies /api/* to the ALB, the frontend uses "/api"
 * as its API base URL (same origin) — no VITE_API_URL override needed.
 */

import { execSync } from "child_process";

function run(cmd) {
  console.log(`> ${cmd}`);
  execSync(cmd, { stdio: "inherit" });
}

// Get account ID
const accountId = execSync(
  "aws sts get-caller-identity --query Account --output text",
  { encoding: "utf-8" },
).trim();

const bucketName = `vswe-frontend-${accountId}`;

// Get CloudFront distribution ID
const distId = execSync(
  `aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='VSWE Frontend'].Id" --output text`,
  { encoding: "utf-8" },
).trim();

if (!distId) {
  console.error(
    "Could not find CloudFront distribution. Is VsweCdn deployed?",
  );
  process.exit(1);
}

console.log(`Deploying to s3://${bucketName}`);
console.log(`CloudFront distribution: ${distId}\n`);

// Build (no VITE_API_URL needed — CloudFront proxies /api/* to ALB)
run("npx vite build");

// Sync to S3
run(`aws s3 sync dist/ s3://${bucketName} --delete`);

// Invalidate CloudFront cache
run(
  `aws cloudfront create-invalidation --distribution-id ${distId} --paths "/*"`,
);

const domain = execSync(
  `aws cloudfront get-distribution --id ${distId} --query "Distribution.DomainName" --output text`,
  { encoding: "utf-8" },
).trim();

console.log(`\nFrontend deployed: https://${domain}`);
