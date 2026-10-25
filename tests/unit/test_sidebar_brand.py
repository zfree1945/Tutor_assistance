"""Regression coverage for the sidebar's text-only application brand."""
from html.parser import HTMLParser
from unittest.mock import patch

from tutor_screen.ui_theme import sidebar_brand


class BrandMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_sidebar_brand_retains_title_and_tagline_without_badge():
    with patch("tutor_screen.ui_theme.st.html") as render:
        sidebar_brand()

    render.assert_called_once()
    markup = BrandMarkup()
    markup.feed(render.call_args.args[0])
    assert markup.text == ["家教筛单器", "本地筛单 · 轻松找到合适订单"]
    assert ("div", {"class": "sidebar-brand"}) in markup.tags
    assert ("strong", {}) in markup.tags
    assert all(tag not in {"span", "img", "svg"} for tag, _ in markup.tags)
