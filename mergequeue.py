#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""GitHub Merge Queue Notifier"""

import asfquart
import quart
import yaml
import aiohttp
import easydict
import time

# GitHub <-> ASF user mappings
GH_MAP_FILE = "/opt/boxer/server/ghmap.yaml"
PUBSUB_URL = "https://pubsub.apache.org:2070/github/mergequeue"
DEFAULT_ORG = "apache"
LOGGED_EVENTS = ("pull_request", "merge_group")
GHMAP_CACHE = []
GHMAP_CACHE_TIME = 3600

class MergeQueueEvent:
    def __init__(self, event_type, payload):
        self.payload = {}
        if event_type not in LOGGED_EVENTS:  # Bail early if we aren't going to log it
            return
        now = int(time.time())
        if not GHMAP_CACHE or GHMAP_CACHE[0] < (now - GHMAP_CACHE_TIME):
            GHMAP_CACHE = [now, yaml.safe_load(open(GH_MAP_FILE, "r"))]
        gh_user_map = GHMAP_CACHE[1]
        self.ed = easydict.EasyDict(payload)
        self.login = payload.get("sender").get("login")  # GitHub User ID
        self.asf_id = gh_user_map.get(self.login, "UNKNOWN-COMMITTER")  # ASF ID if mappings work
        self.repository = self.ed.repository.name
        if event_type == "pull_request":
            self.payload = {
                "organization": self.ed.organization,
                "repository": self.repository,
                "merge_queue_action": self.ed.action,
                "actor": self.login,
                "actor_asf": self.asf_id,
                "pr": self.ed.pull_request,
                "title": self.ed.pull_request.title,
            }
            if self.ed.action == "enqueued":
                self.payload["message"] = (
                    f"User {self.login} (Apache ID {self.asf_id}) added PR #{self.ed.number} ({self.ed.pull_request.title}) to merge queue."
                )
            elif self.ed.action == "dequeued":
                mcs = self.ed.pull_request.get("merge_commit_sha", "0000000")
                self.payload["merge_commit_sha"] = mcs
                self.payload["message"] = (
                    f"User {self.login} (Apache ID {self.asf_id}) merged PR #{self.ed.number} ({self.ed.pull_request.title}). Merge SHA is {mcs}"
                )

        elif event_type == "merge_group":
            if self.ed.action == "checks_requested":
                merge_group = payload.get("merge_group", {})
                base = merge_group.get("base_sha")
                head = merge_group.get("head_sha")
                message = f"User {self.login} (Apache ID {self.asf_id}) merged commits after {base} up until {head} via merge queue"
                self.payload = {
                    "organization": self.ed.organization,
                    "repository": self.repository,
                    "merge_queue_action": self.ed.action,
                    "actor": self.login,
                    "actor_asf": self.asf_id,
                    "message": message,
                }

    async def pubsub(self):
        """Publishes MQ payload to PubSub if valid"""
        if self.payload and self.payload.get("message"):
            async with aiohttp.ClientSession() as client:
                try:
                    await client.post(f"{PUBSUB_URL}/{self.repository}", json=self.payload)
                    print(f"Posted pubsub payload to {PUBSUB_URL}/{self.repository}")
                except aiohttp.ClientError as e:
                    print(f"Pubsub client error: {e}")


def main():
    app = asfquart.construct("merge-queue-webhook")

    @app.route("/webhook", methods=["POST"])
    async def webhook_receiver():
        payload = await quart.request.get_json()
        event_type = quart.request.headers.get("X-Github-Event")
        event = MergeQueueEvent(event_type, payload)
        await event.pubsub()
        return quart.jsonify({"message": "Webhook received successfully"})

    return app


# python3 server.py (runs Quart debug server)
if __name__ == "__main__":
    app = main()
    app.runx(port=8085)
else:
    # Hypercorn or some such
    app = main()
