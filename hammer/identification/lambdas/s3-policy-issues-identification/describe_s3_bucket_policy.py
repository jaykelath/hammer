import json
import logging

from library.logger import set_logging
from library.config import Config
from library.aws.s3 import S3BucketsPolicyChecker
from library.aws.utility import Account
from library.ddb_issues import IssueStatus, S3PolicyIssue
from library.ddb_issues import Operations as IssueOperations


def lambda_handler(event, context):
    """ Lambda handler to evaluate s3 buckets policy """
    set_logging(level=logging.INFO)

    try:
        payload = json.loads(event["Records"][0]["Sns"]["Message"])
        account_id = payload['account_id']
        account_name = payload['account_name']
    except Exception:
        logging.exception(f"Failed to parse event\n{event}")
        return

    try:
        config = Config()

        main_account = Account(region=config.aws.region)
        ddb_table = main_account.resource("dynamodb").Table(config.s3policy.ddb_table_name)

        account = Account(id=account_id,
                          name=account_name,
                          role_name=config.aws.role_name_identification)
        if account.session is None:
            return

        logging.debug(f"Checking for public S3 policies in {account}")

        # existing open issues for account to check if resolved
        open_issues = IssueOperations.get_account_open_issues(ddb_table, account_id, S3PolicyIssue)
        # make dictionary for fast search by id
        # and filter by current region
        open_issues = {issue.issue_id: issue for issue in open_issues}
        logging.debug(f"S3 in DDB:\n{open_issues.keys()}")

        checker = S3BucketsPolicyChecker(account=account)
        if not checker.check():
            return

        for bucket in checker.buckets:
            logging.debug(f"Checking {bucket.name}")
            if bucket.public:
                issue = S3PolicyIssue(account_id, bucket.name)
                issue.issue_details.owner = bucket.owner
                issue.issue_details.tags = bucket.tags
                issue.issue_details.policy = bucket.policy
                if config.s3policy.in_whitelist(account_id, bucket.name):
                    issue.status = IssueStatus.Whitelisted
                else:
                    issue.status = IssueStatus.Open
                logging.debug(f"Setting {bucket.name} status {issue.status}")
                IssueOperations.update(ddb_table, issue)
                # remove issue id from issues_list_from_db (if exists)
                # as we already checked it
                open_issues.pop(bucket.name, None)

        logging.debug(f"S3 in DDB:\n{open_issues.keys()}")
        # all other unresolved issues in DDB are for removed/remediated buckets
        for issue in open_issues.values():
            IssueOperations.set_status_resolved(ddb_table, issue)
    except Exception:
        logging.exception(f"Failed to check s3 policies for '{account_id} ({account_name})'")
        return

    logging.debug(f"Checked s3 policies for '{account_id} ({account_name})'")