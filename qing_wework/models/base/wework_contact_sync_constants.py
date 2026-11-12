# -*- coding: utf-8 -*-

BINDING_STATES = [
    ("active", "Active"),
    ("conflict", "Conflict"),
]

SYNC_JOB_STATES = [
    ("pending", "Pending"),
    ("waiting_department", "Waiting for Department"),
    ("running", "Running"),
    ("done", "Done"),
    ("retry", "Retry"),
    ("conflict", "Conflict"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]

# Keep the existing push values so jobs created before the upgrade remain valid.
SYNC_JOB_TYPES = [
    ("department", "Push Department"),
    ("user", "Push User"),
    ("department_pull", "Pull Departments"),
    ("user_pull", "Pull Users"),
]
