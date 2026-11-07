from __future__ import annotations

import pulumi

import core as s

web_name = f"{s.prefix}-web"
# An externally enabled app uses this same FQDN for same-environment callbacks.
azure_host = pulumi.Output.concat(web_name, ".", s.app_environment.default_domain)
restrictions = [
    {"name": f"allowed-{index}", "ip_address_range": cidr, "action": "Allow"}
    for index, cidr in enumerate(s.allowed_ips)
]
