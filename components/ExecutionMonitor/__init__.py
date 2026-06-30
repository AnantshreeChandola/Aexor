"""
ExecutionMonitor — Infrastructure Watchdog

Background polling service that detects stuck executions and enforces
time budgets. Infrastructure failures are terminal — no replay, user
must start a new plan.

Components:
    TrackerService  — Non-fatal write API for ExecuteOrchestrator
    MonitorService  — Background loop that polls for stuck/timed-out executions
"""
