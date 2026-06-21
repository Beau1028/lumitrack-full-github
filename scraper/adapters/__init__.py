from scraper.adapters.base_adapter import BaseAdapter
from scraper.adapters.bookly_adapter import BooklyAdapter
from scraper.adapters.catalog_adapter import CatalogAdapter
from scraper.adapters.codek_adapter import CodeKAdapter
from scraper.adapters.cubeescape_adapter import CubeEscapeAdapter
from scraper.adapters.deepthinker_adapter import DeepthinkerAdapter
from scraper.adapters.earthstar_adapter import EarthstarAdapter
from scraper.adapters.frank_adapter import FrankAdapter
from scraper.adapters.generic_adapter import GenericAdapter
from scraper.adapters.horror_switch_adapter import HorrorSwitchAdapter
from scraper.adapters.keyescape_adapter import KeyescapeAdapter
from scraper.adapters.naver_booking_adapter import NaverBookingAdapter
from scraper.adapters.murderparker_adapter import MurderParkerAdapter
from scraper.adapters.amazed_adapter import AmazedAdapter
from scraper.adapters.page_today_adapter import PageTodayAdapter
from scraper.adapters.play33_adapter import Play33Adapter
from scraper.adapters.oasis_adapter import OasisAdapter
from scraper.adapters.sinbi_adapter import SinbiAdapter
from scraper.adapters.shortstories_adapter import ShortstoriesAdapter
from scraper.adapters.xdungeon_adapter import XdungeonAdapter
from scraper.adapters.zero_world_adapter import ZeroWorldAdapter
from scraper.adapters.ticket_to_escape_adapter import TicketToEscapeAdapter
from scraper.adapters.wordpress_booked_adapter import WordPressBookedAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "catalog": CatalogAdapter,
    "bookly": BooklyAdapter,
    "codek": CodeKAdapter,
    "cubeescape": CubeEscapeAdapter,
    "deepthinker": DeepthinkerAdapter,
    "earthstar": EarthstarAdapter,
    "frank": FrankAdapter,
    "generic": GenericAdapter,
    "horror_switch": HorrorSwitchAdapter,
    "keyescape": KeyescapeAdapter,
    "naver_booking": NaverBookingAdapter,
    "murderparker": MurderParkerAdapter,
    "amazed": AmazedAdapter,
    "page_today": PageTodayAdapter,
    "play33": Play33Adapter,
    "oasis": OasisAdapter,
    "sinbi": SinbiAdapter,
    "shortstories": ShortstoriesAdapter,
    "xdungeon": XdungeonAdapter,
    "zero_world": ZeroWorldAdapter,
    "ticket_to_escape": TicketToEscapeAdapter,
    "wordpress_booked": WordPressBookedAdapter,
    "blocked": CatalogAdapter,
    "limited": CatalogAdapter,
    "permission_required": CatalogAdapter,
}


def get_adapter(adapter_type: str) -> BaseAdapter:
    try:
        adapter_class = ADAPTERS[adapter_type]
    except KeyError as exc:
        supported = ", ".join(sorted(ADAPTERS))
        raise ValueError(
            f"Unknown adapter_type '{adapter_type}'. Available: {supported}"
        ) from exc
    return adapter_class()


__all__ = [
    "BaseAdapter",
    "BooklyAdapter",
    "CatalogAdapter",
    "CodeKAdapter",
    "CubeEscapeAdapter",
    "DeepthinkerAdapter",
    "EarthstarAdapter",
    "FrankAdapter",
    "GenericAdapter",
    "HorrorSwitchAdapter",
    "KeyescapeAdapter",
    "NaverBookingAdapter",
    "MurderParkerAdapter",
    "AmazedAdapter",
    "PageTodayAdapter",
    "Play33Adapter",
    "OasisAdapter",
    "SinbiAdapter",
    "ShortstoriesAdapter",
    "XdungeonAdapter",
    "ZeroWorldAdapter",
    "TicketToEscapeAdapter",
    "WordPressBookedAdapter",
    "get_adapter",
]
