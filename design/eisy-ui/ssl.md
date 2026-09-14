Hi Michel,

Here’s the status on public SSL certificate for eisy-ui.

I can reliably issue public certificates using certbot/let’s encrypt. I issue it for {uuid-without-colons}.isy.ui (example for my unit: 0021b9025fc2.isy.io)

I can also create a Route 53 A record for 0021b9025fc2.isy.io that points to the local IP.

This solution allows for a totally local access with a certificate recognized by the browser. 
I have this currently automated on startup, with a fallback to the self-signed cert.
 

I think this solution works fine, with the following caveats:
1.	PG3x needs to be disabled – We can’t have PG3x in an iframe with a self-signed cert, the browser will tell you it’s not secure.
2.	This exposes the eisy’s private IP address, as this is in a public DNS. Technical users may not like that.
3.	Some routers or DNS resolvers may have security rules that would block DNS resolution to a local IP. We can detect that.

The only solution to avoid the public DNS are these:
1.	Have users create a DNS entry in their router, or worse, to have them create the entry in host files.
2.	Also possible would be to have eisy resolve for {uuid-without-colons}.isy.ui to itself, with forwarders to the ISP, and have the router resolve through eisy. But that’s even more complex than option 1. I don’t recommend it.

What I would suggest is this:
Add an “Advanced networking” page where we can see and change these settings.
1.	Have a switch to enable/disable public certificates for {uuid}.isy.io. Default on. When enabled, it would auto-create the cert (and auto-renew when needed).
2.	If the above switch is enabled, have a second switch to enable/disable resolution to {uuid.isy.ui }. Default On. When turned on, the A record would be created in Route 53. When turned off, that would remove the DNS entry, and provide generic instructions on how to setup the local DNS.

In addition to all of this, I would do the following:
http://eisy.local and https://eisy.local would still work. If public certificates is active and certs were created successfully, it would auto-redirect to https://{uuid}.isy.io

This way, they would still have the convenience of just entering eisy.local in their browser, but would end up on https://{uuid}.isy.io

Let me know what you think of all this before I finalize.
