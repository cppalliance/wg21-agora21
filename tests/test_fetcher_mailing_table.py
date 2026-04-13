"""Mailing table HTML parsing (no network; :mod:`agora21.fetcher`)."""

from agora21.fetcher import parse_papers_for_mailing_from_html


def test_parse_mailing_table_fixture():
    html = """
    <html><body>
    <a id="mailing2026-02"></a>
    <table>
    <tr>
      <td><a href="p0001r0.html">p0001r0</a></td>
      <td>Example title</td>
      <td>Author One</td>
      <td>2026-01-10</td>
      <td>2026-02</td>
      <td></td>
      <td>EWGI</td>
      <td></td>
    </tr>
    <tr>
      <td><a href="p2900r10.pdf">p2900r10</a></td>
      <td>Other</td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td></td>
    </tr>
    </table>
    </body></html>
    """
    page = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2026/"
    papers = parse_papers_for_mailing_from_html(html, "2026-02", page)
    ids = [p["paper_id"] for p in papers]
    assert "p0001r0" in ids and "p2900r10" in ids
    p0 = next(p for p in papers if p["paper_id"] == "p0001r0")
    assert p0["title"] == "Example title"
    assert p0["subgroup"] == "EWGI"
    assert p0["url"].endswith("p0001r0.html")
