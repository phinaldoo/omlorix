# Weather

**Weather** resolves a location and returns current conditions and forecasts using the configured weather service. It does not provide official emergency alerts.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then run the location-specific checks below.

## Configure and test

1. Open **Admin Settings > Tools > Weather**.
2. Choose **Open-Meteo** for its public keyless forecast endpoint or **OpenWeatherMap** and enter its API key.
3. For OpenWeatherMap, choose **Free API** for current conditions plus the 5-day/3-hour forecast, or **One Call 3.0** only when the account has that API access.
4. Choose **Open-Meteo Geocoding** for production. The optional **api-bdc Reverse Geocoding** choice is subject to the limitation below.
5. Enable the tool, then select **Weather** on the models that may use it.
6. Test a city, a city with a country, coordinates, and an ambiguous place.

Location text and coordinates may be sent to weather and geocoding providers. Review their retention, terms, region, and quota. Allow their destinations through [Outbound Network Access](../3_admin_settings/3_1_outbound_network_access.md).

Omlorix currently uses Open-Meteo's public keyless [forecast](https://open-meteo.com/en/docs) and [geocoding](https://open-meteo.com/en/docs/geocoding-api) endpoints; the form cannot configure Open-Meteo's commercial customer endpoint or API key. Open-Meteo documents its public free access for non-commercial use and reserves customer resources for commercial plans, so confirm licensing and capacity before production or use the configured OpenWeatherMap integration.

The **api-bdc Reverse Geocoding** option calls BigDataCloud's keyless `reverse-geocode-client` endpoint from the Omlorix backend. [BigDataCloud documents that endpoint](https://www.bigdatacloud.com/geocoding-apis/free-reverse-geocode-to-city-api) for direct browser or mobile-client traffic only and warns that server-side requests can be blocked. Omlorix does not currently expose credentials for BigDataCloud's server-side Reverse Geocoding API. Do not select api-bdc in production unless BigDataCloud has explicitly authorized this request pattern for your deployment; this is a product-integration limitation, not an outbound-policy setting.

Forecasts may be delayed or wrong. Tell users to consult official sources for severe weather, aviation, marine, emergency, or other safety-critical decisions.

If a location is wrong, ask for country or region rather than relying on model inference. If no data appears, check the selected forecast and geocoding providers, OpenWeatherMap mode and entitlement, key, quota, outbound policy, and saved provider choice.
