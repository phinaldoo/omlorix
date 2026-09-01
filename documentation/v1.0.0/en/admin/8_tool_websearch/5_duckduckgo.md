# DuckDuckGo

**DuckDuckGo** provides keyless, best-effort web result discovery. It does not read result pages, so pair it with a Scrape provider.

Use the shared controls and rollout sequence in [Web Search Providers](1_introduction.md); this page covers DuckDuckGo's keyless search behavior.

Configure the visible **Fallback language**, **Fallback country**, **Forward user locale**, **Max search results**, and **Safesearch level** settings. Locale affects ranking; SafeSearch is a preference, not a security or compliance control.

The underlying search route can change and may be throttled or blocked. Use it for low-volume, non-sensitive workloads that can tolerate intermittent or changed results. Use a contracted hosted provider, self-hosted SearXNG, or Custom gateway when processor identity, a service commitment, or predictable billing matters.

Test the fallback locale, representative user locales when forwarding is enabled, expected burst volume, and the assigned scraper. If URLs appear without page content, troubleshoot the scraper. If results become intermittent, lower volume or move to a provider with an account and support agreement.
