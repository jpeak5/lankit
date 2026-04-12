# lankit — Mission

## The niche

Home networking tools tend to cluster at two extremes, and the gap between them is where most technically capable people get stuck.

On the easy end: consumer systems like Eero, Google Nest, and Orbi. They work well, they're manageable from a phone app, and they require almost no knowledge to operate. But they treat the network as a single flat space. Every device — your laptop, your smart TV, your kid's tablet, the cheap thermostat you got from Amazon — shares the same broadcast domain. There's no meaningful way to say "this device can't see that one." There's no config file to put in version control. There's no Pi-hole integration. There's no rollback. The vendor controls the software, and your job is to stay out of the way.

On the difficult end: pfSense, OPNsense, and their relatives. These are genuinely powerful platforms. They can do almost anything you'd want a router to do, and they do it correctly. But they assume you're running x86 hardware (a separate box, not a MikroTik), they have sprawling web UIs where the relationship between concepts isn't always obvious, and there's no "one file" that describes your network. Setup is a multi-hour exercise in navigating menus and hoping you haven't missed a step. The configuration lives in a database, not a text file.

And then there's raw MikroTik. RouterOS is remarkably capable for the price. The hAP ax³ — about $100 — runs a full enterprise-grade routing stack, handles VLAN-isolated WiFi without additional APs, and has native support for WireGuard, DNSSEC, and firewall policies that rival commercial gear. But the RouterOS CLI is genuinely arcane. Commands require remembering specific table paths (`/ip firewall filter add chain=forward ...`), concepts like bridge-VLAN tables are not explained anywhere in context, and there's no reproducibility story: you configure it, you hope you wrote down what you did, and if you need to rebuild it from scratch you're doing it again from memory and forum posts.

lankit occupies the space between those two worlds. It's for people who want real network segmentation — actual VLAN isolation, actual DNS filtering, actual firewall policy — without needing to become a network engineer. The mechanism is a single YAML file (`network.yml`) that describes what you want in plain terms. From that file, lankit generates all the RouterOS commands, all the Ansible configuration for Pi-hole and Unbound, all the firewall rules, and a plain-English summary of those rules you can share with your household. You don't touch the RouterOS CLI directly. You don't write Ansible by hand. You describe what you want, run `lankit apply`, and it handles the translation.

That's the niche: the tooling layer that makes MikroTik's capabilities accessible to people who could understand them if explained clearly, but don't want to spend weeks learning RouterOS syntax before they get a working network.

---

## Who it's for

The target person has a technical background but not necessarily a networking one. They write code, or they work in IT, or they've built a home lab for other purposes. They've heard of VLANs. They understand the general idea that network segments keep devices separated. They've looked at their router's web UI and noticed there's a "VLAN" section and they don't know what to put in it. They've probably tried one of the following:

- Set up an Eero or similar and felt vaguely uncomfortable that all their IoT devices share a network with their NAS.
- Started reading pfSense documentation and given up when they realized they'd need to buy different hardware and start over.
- Watched a YouTube video about VLANs on MikroTik and gotten about forty minutes in before the presenter started typing `/interface bridge port add interface=vlan-trusted bridge=bridge1 frame-types=admit-only-vlan-tagged` and felt it slip away.
- Succeeded at setting something up manually, but now they're not sure what they actually configured, they can't reproduce it, and they're afraid to touch it.

What they want is to be able to say: "my smart TV goes on the media segment, my IoT sensors go on the IoT segment, IoT can't reach the media segment, trusted devices can reach everything, and guest WiFi is completely isolated." And they want that to just work, with rollback if it doesn't.

They're willing to invest time upfront. DESIGN.md estimates around ten hours for a complete first setup — that's an honest number, and it's an investment this person will make if the payoff is real. What they won't do is debug cryptic router errors for three days with no clear path forward.

**Who lankit is not for:**

It's not for people who want a GUI. lankit is a CLI tool. There are web portals planned for day-to-day management, but initial setup and ongoing configuration changes go through a YAML file and terminal commands. If that's not acceptable, a different tool is the right answer.

It's not for people with Ubiquiti networks, Cisco gear, or custom pfSense boxes. lankit generates RouterOS scripts. It doesn't know how to speak to anything else. Expanding hardware support would fundamentally change the scope of what it is.

It's not for people who want zero complexity. A VLAN-segmented network with Pi-hole and Unbound requires a Raspberry Pi, an SSH key, a specific router model, and a working understanding of what the segments you're configuring actually do. lankit makes this much easier, but it doesn't eliminate the inherent complexity of the thing you're building.

It's not for network administrators managing production infrastructure. lankit is residential scale, intentionally. It doesn't support multiple routers, it doesn't support BGP or OSPF, it doesn't have an API, and it's not designed for high availability. If you need those things, you're past the problem lankit solves.

---

## Why it matters (the greater good)

Most households accept a flat network not because they want one, but because the alternative has been impractical. This isn't a trivial concession.

IoT devices are the most concrete problem. The average household has somewhere between ten and thirty internet-connected devices that are not computers: smart speakers, cameras, thermostats, light switches, TVs, streaming boxes, printers, appliances. Many of these devices have poor security records. They run outdated firmware, they don't receive patches, they communicate with home servers whose privacy policies are written to protect the company and not the user. On a flat network, any one of them is a potential pivot point to reach your NAS, your work laptop, or anything else on the same subnet.

Network segmentation fixes this structurally. An IoT device on the `iot` segment can reach the internet to do its job, but it can't initiate a connection to anything on the `trusted` or `servers` segments. If it gets compromised, the damage is contained. It can't scan your file shares. It can't reach your home automation server unless you explicitly allow it. The firewall enforces this at the router level — the IoT device doesn't get a vote.

The same logic applies to guest WiFi. A visitor's phone on your main network has the same network-level access as your own devices. With client isolation on a dedicated `guest` segment, they get internet access and nothing else. Their device can't see yours. Yours can't see theirs. This is how hotel WiFi has worked for twenty years, but most home setups don't bother because it's too hard to configure.

DNS filtering compounds both of these. Pi-hole running on a Raspberry Pi intercepts every DNS query on your network and drops the ones that resolve to known ad servers, tracking domains, and malware endpoints. This isn't just about ads — it's the fastest way to get visibility into what your IoT devices are actually talking to. The `lankit audit` command tells you which devices are communicating with what; the query logs tell you when your thermostat decided to phone home to a third-party analytics platform at 3 AM.

None of this is new technology. Enterprise networks have had VLAN isolation, DNS filtering, and firewall policy for decades. What lankit does is make the configuration of these things approachable for someone running a home network with a MikroTik router and a Raspberry Pi. The technology was already democratized in hardware. lankit is an attempt to democratize the configuration.

---

## The non-negotiables

These are the things lankit has to be. If any of them are removed or compromised significantly, lankit becomes a different product — and not necessarily a better one.

**One config file.** `network.yml` is the complete description of your network. Everything — the RouterOS scripts, the Pi-hole configuration, the Unbound configuration, the firewall rules, the plain-English explanation of those rules, the rollback card — is generated from it. This is not just a convenience. It means your network is reproducible. You can check it into version control. You can diff two versions and see exactly what changed. You can delete your router and rebuild from scratch. If that property is broken — if some configuration has to live in the router's flash memory or be set manually through WinBox — lankit's core promise doesn't hold.

**Rollback-first safety.** Every `lankit apply` snapshots the router state before making changes. If something goes wrong, `lankit rollback` brings it back. RouterOS safe mode provides a second line of defense: if you don't confirm changes within the configured window, the router reverts automatically. The `lankit rollback-card` command generates a printed reference for the scenario where you can't reach the router at all. Safety has to be assumed, not optional, because the failure mode of a misconfigured home router is losing access to your own network from a remote location.

**Plain-English output.** `lankit explain` generates a human-readable description of every segment's rules. `lankit rules` renders firewall policy in plain terms. `lankit matrix` shows a grid of what can reach what. These aren't debugging tools — they're the primary way a user verifies that what they wrote in `network.yml` produces the network they intended. A tool that only generates opaque router config and asks you to trust it is a tool most people will misconfigure and not notice.

**No manual drift.** All network state goes through lankit. Ansible manages the Pi. The RouterOS scripts manage the router. If you make a manual change through WinBox or SSH, `lankit audit` will flag it as drifted or rogue. This constraint is what makes the config-as-code property real: if manual changes are acceptable, then `network.yml` is no longer the source of truth, it's just a starting point, and reproducibility is an illusion.

**Residential scale.** lankit doesn't try to be enterprise software. It supports one router, one or two Raspberry Pis, and the segments a household actually needs. Keeping this constraint is what makes the tool approachable. Every feature added for an edge case that most users don't have makes the tool harder for the users it already serves.

---

## Revision history

- `2026-04-12`: Initial document created from DESIGN.md and codebase analysis.
