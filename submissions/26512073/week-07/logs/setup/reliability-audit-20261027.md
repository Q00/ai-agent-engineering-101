# Reliability audit before Week 8

Read-only inspection found that the latest saved kit session used the public URL
`https://corrected-janet-rest-popularity.trycloudflare.com` and ended with a
shutdown/KeyboardInterrupt at 23:02:55 on October 26. Current offline status
after that stop must not be described as a new spontaneous outage.

Earlier repair records independently captured public NXDOMAIN responses while
the local seller and kit proxy still served HTTP 200. Those incidents establish
public reachability failures, not a failure caused by a particular sale count.

For the latest session's model calls (filename timestamps 20261026T125116 through
20261026T140300), 47 seller offer calls and 13 pricing calls were found. Of these,
16 completed successfully and 44 logged TIMEOUT. Up to three timed-out 90-second
intervals overlapped. This estimate does not prove that concurrency caused the
timeouts. Saved diagnostics did not identify a definitive service/network cause.

A fresh isolated real harness call on October 27 returned {"status":"ok"} in
8.39 seconds with the normal harness and a 30-second deadline. It created no
market trade. Its raw harness output and the separate JSON result are preserved.
One successful isolated call is not a load or long-duration reliability test.

The student has a Windows PC only. Public address continuity and reliable model
execution are separate problems. Keep existing identity keys and pricing history.
Do not replace timed-out decisions with fabricated successful trades.

Hosting options checked against official documentation:

- Cloudflare Quick Tunnels have temporary URLs and no uptime guarantee:
  https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
- Ngrok's free account includes one assigned development domain and no endpoint
  time limit, but is limited to 20,000 HTTP requests and 1 GB outbound per month:
  https://ngrok.com/docs/pricing-limits/free-plan-limits

A stable domain does not keep a sleeping PC online or remove model latency.
No hosting account was created, no software was installed, and no agent was
rejoined during this audit. Next steps require the student's deployment choice
and controlled request-rate/concurrency checks before changing model settings.
