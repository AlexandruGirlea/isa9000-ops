from __future__ import annotations

worker_class = "gthread"
workers = 2
threads = 4
bind = "0.0.0.0:8000"
timeout = 60
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
forwarded_allow_ips = "*"
accesslog = "-"
access_log_format = "%(s)s %(M)sms"


def post_fork(server, worker):
    from shared import telemetry

    telemetry.setup("isa9000-web")
