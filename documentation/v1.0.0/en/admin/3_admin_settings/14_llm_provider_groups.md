# Provider Groups

**Admin Settings > Provider Groups** combines compatible chat providers behind one model assignment for weighted random distribution with health-aware failover.

## Requirements

A provider group must contain at least two enabled providers of the same type that support chat models. Omlorix can expose only models available from every selected provider.

Use a group only when the providers are operationally interchangeable. They should have compatible model behavior, context limits, regional policy, logging expectations, and tool support. Grouping providers does not make their privacy or residency terms equivalent.

## Create a Group

1. Select **New Provider Group**.
2. Enter **Group Name** and choose an **Icon**.
3. Under **Member Providers**, select **Add Provider** and add at least two eligible providers. The first selection locks the provider type.
4. Set **Weight for** each member.
5. Select **Save Group**, then create or reassign a [Model](15_0_llm_models.md) to the group.
6. Test normal requests and failure behavior before broad access.

Weights express relative preference for random selection; they do not guarantee an exact traffic split over a short period. Failover skips unavailable or already failed members, but it cannot hide incompatible responses, model catalogs, quotas, or a shared upstream outage.

## Changes and Deletion

Removing or disabling a provider can update its groups. If fewer than two eligible providers remain, the group and models that depend on it can be removed as part of the provider deletion. Review the provider-deletion impact carefully.

A provider group cannot be deleted while an Omlorix model uses it. Reassign or delete every dependent model first, then delete the group.

After changing membership, weights, or order, test the common model inventory and observe provider errors and latency. Keep a direct provider model available during rollout when a simple rollback path is important.
