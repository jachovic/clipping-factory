from .base import CampaignData, MarketplaceAdapter, SubmitResult
from .content_rewards import ContentRewardsAdapter
from .manual import ClipsterAdapter, PrimeOraclesAdapter, PromoteFunAdapter
from .vyro import VyroAdapter
from .whop import WhopAdapter

ADAPTERS: dict[str, type[MarketplaceAdapter]] = {
    a.name: a for a in (ContentRewardsAdapter, ClipsterAdapter, VyroAdapter, WhopAdapter,
                        PromoteFunAdapter, PrimeOraclesAdapter)
}


def get_adapter(name: str) -> MarketplaceAdapter:
    return ADAPTERS[name]()


__all__ = ["ADAPTERS", "get_adapter", "CampaignData", "MarketplaceAdapter", "SubmitResult"]
