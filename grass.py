from mapgen import DIRT


class GrassTile:
    """
    Represents a grass tile capable of spreading to adjacent dirt over time.
    Inspired by Minecraft's dirt-to-grass mechanic.

    Not yet wired into the simulation loop — stub for future implementation.
    """

    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y
        self.growth_timer = 0.0  # seconds until next spread attempt

    def update(self, dt: float, neighbors: dict[str, str]) -> None:
        """
        Called each simulation tick. Will drive grass-spreading logic.

        Args:
            dt:        Delta time in seconds since last tick.
            neighbors: Dict of direction → terrain string from MapGenerator.get_neighbors().
        """
        pass  # TODO: accumulate growth_timer, attempt spread when threshold reached

    def can_spread_to(self, neighbor_terrain: str) -> bool:
        """Returns True if grass should spread to a neighboring tile of this terrain type."""
        return neighbor_terrain == DIRT
