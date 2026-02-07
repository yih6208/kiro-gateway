"""
Estimated cost calculation based on Anthropic API pricing.

Prices are per million tokens (MTok) in USD.
"""

from typing import Optional

# (input_price_per_mtok, output_price_per_mtok)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "opus-4.6": (5.0, 25.0),
    "opus-4.5": (5.0, 25.0),
    "opus-4.1": (15.0, 75.0),
    "opus-4": (15.0, 75.0),
    "sonnet-4.5": (3.0, 15.0),
    "sonnet-4": (3.0, 15.0),
    "sonnet-3.7": (3.0, 15.0),
    "haiku-4.5": (1.0, 5.0),
    "haiku-3.5": (0.80, 4.0),
    "opus-3": (15.0, 75.0),
    "sonnet-3.5": (3.0, 15.0),
    "haiku-3": (0.25, 1.25),
}


def _match_model(model_name: str) -> Optional[tuple[float, float]]:
    """
    Match a model name to its pricing tier using substring matching.

    Handles variations like claude-sonnet-4-5, claude-sonnet-4.5,
    claude-3-5-sonnet, etc.
    """
    if not model_name:
        return None

    name = model_name.lower().replace("_", "-")

    # Try each pricing key as a substring match
    for key, prices in MODEL_PRICING.items():
        # Normalize key for comparison (e.g., "opus-4.5" -> also match "opus-4-5")
        key_dash = key.replace(".", "-")
        if key in name or key_dash in name:
            return prices

    # Handle older naming convention: claude-3-5-sonnet -> sonnet-3.5
    # Extract family and version from patterns like "3-5-sonnet" or "3-sonnet"
    for family in ("opus", "sonnet", "haiku"):
        if family in name:
            # Try to find version numbers near the family name
            import re
            # Match patterns like 3.5, 3-5, 4.5, 4-5, 3.7, 3-7, 4, 3, 4.1, 4-1
            versions = re.findall(r"(\d+)[-.](\d+)", name)
            if versions:
                for major, minor in versions:
                    candidate = f"{family}-{major}.{minor}"
                    if candidate in MODEL_PRICING:
                        return MODEL_PRICING[candidate]
            # Single version number
            single_versions = re.findall(r"(\d+)", name)
            for v in single_versions:
                candidate = f"{family}-{v}"
                if candidate in MODEL_PRICING:
                    return MODEL_PRICING[candidate]

    return None


def calculate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> dict[str, Optional[float]]:
    """
    Calculate estimated cost for token usage.

    Args:
        model: Model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Dict with input_cost, output_cost, total_cost (None if model unknown)
    """
    prices = _match_model(model)
    if prices is None:
        return {"input_cost": None, "output_cost": None, "total_cost": None}

    input_price, output_price = prices
    input_cost = (input_tokens / 1_000_000) * input_price
    output_cost = (output_tokens / 1_000_000) * output_price
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }
