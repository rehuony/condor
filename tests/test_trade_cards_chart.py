"""Real chart rendering uses shipped fonts regardless of the working directory."""

from io import BytesIO
from pathlib import Path

import pytest
from matplotlib.figure import Figure
from matplotlib.text import Text
from PIL import Image

from condor.trade_cards import chart, render_chart
from tests.support.trade_cards import plan


@pytest.mark.filterwarnings("error:Glyph .* missing from font.*:UserWarning")
@pytest.mark.parametrize(
    "side,entry_low,entry_high,stop,tp1,tp2",
    [
        ("long", "99", "101", "95", "105", "110"),
        ("short", "99", "101", "105", "95", "90"),
        ("long", "0.000099", "0.000101", "0.000095", "0.000105", "0.000110"),
    ],
    ids=["long", "short", "small-price"],
)
def test_chart_renders_chinese_with_shipped_fonts_from_any_directory(
    monkeypatch, tmp_path, side, entry_low, entry_high, stop, tp1, tp2
):
    fonts = Path(chart.__file__).resolve().parent / "fonts"
    rendered_text = []
    savefig = Figure.savefig

    def inspect_fonts(figure, *args, **kwargs):
        for text in figure.findobj(Text):
            if text.get_text():
                filename = text.get_fontproperties().get_file()
                assert filename is not None
                font = Path(filename).resolve()
                assert font.is_relative_to(fonts) and font.is_file()
                rendered_text.append(text.get_text())
        result = savefig(figure, *args, **kwargs)
        boxes = sorted(
            (
                text.get_window_extent(figure.canvas.get_renderer())
                for text in figure.findobj(Text)
                if text.get_position()[1] == 1030
            ),
            key=lambda box: box.x0,
        )
        assert len(boxes) == 4
        assert all(0 <= box.x0 < box.x1 <= figure.bbox.width for box in boxes)
        assert all(left.x1 + 8 <= right.x0 for left, right in zip(boxes, boxes[1:]))
        return result

    monkeypatch.setattr(Figure, "savefig", inspect_fonts)
    monkeypatch.chdir(tmp_path)
    midpoint = (float(entry_low) + float(entry_high)) / 2
    candles = [
        {
            "timestamp": 1_790_000_000 + index * 3600,
            "open": midpoint * 0.995,
            "high": midpoint * 1.015,
            "low": midpoint * 0.985,
            "close": midpoint * (1 + (index % 3 - 1) * 0.005),
        }
        for index in range(30)
    ]
    png = render_chart(
        candles,
        plan(
            side=side,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_price=stop,
            tp1_price=tp1,
            tp2_price=tp2,
        ),
        "hyperliquid_perpetual",
    )
    assert "交 易 计 划" in rendered_text
    # The footer names the connector the candles came from, not a fixed venue.
    assert any(text.endswith("· Hyperliquid Perp") for text in rendered_text)
    assert ("做多  LONG" if side == "long" else "做空  SHORT") in rendered_text
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (1024, 1280)
        image.load()
