"""Feature documentation: every module belongs to a feature, each has a design and a diagram, Buddy can show it."""

import json
import os
import re

import pytest

from buddy import codedocs, makers
from buddy import codedocs_features as cf
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture(scope="module")
def features():
    return cf.build()


def test_every_server_module_belongs_to_exactly_one_feature():
    here = os.path.dirname(codedocs.__file__)
    modules = {f for f in os.listdir(here) if f.endswith(".py") and f != "__init__.py"}
    listed = [f for _, _, files in codedocs.COMPONENTS for f in files]
    assert sorted(modules - set(listed)) == [], "add these modules to a feature in codedocs.COMPONENTS"
    assert sorted(set(listed) - modules) == [] and len(listed) == len(set(listed))


def test_each_feature_has_a_design_and_what_it_keeps_and_calls(features):
    by = {f["slug"]: f for f in features}
    assert len(features) == len(codedocs.COMPONENTS) and all(f["modules"] and f["role"] for f in features)
    inv = by["investing"]
    tools = {t["name"] for m in inv["modules"] for t in m["tools"]}
    assert {"recommendations", "community_signals", "investor_checks", "fund_holdings", "paper_portfolio"} <= tools
    assert any("finnhub" in m["services"] for m in inv["modules"])
    text = cf.design_text(inv)
    assert text.startswith("Investing: signals, research and practice") and "[recommend.py]" in text
    assert "Outside services (through Buddy Connect):" in text and len(text) <= cf.MAX_TEXT
    assert "Buddy Connect" in " ".join(inv["uses_features"]) or "The price book" in " ".join(inv["uses_features"])
    deals = by["shopping-and-deals"]
    assert "The price book, pattern learning and trained models" in deals["uses_features"]
    assert "Shopping and deals" in by["the-price-book-pattern-learning-and-trained-models"]["used_by_features"]


def test_a_question_finds_its_feature(features):
    def name(q):
        f = cf.find(q, features)
        return f["slug"] if f else None
    assert name("how does the deals collector work") == "shopping-and-deals"
    assert name("explain the investing signals and paper trades") == "investing"
    assert name("how do you decide which alerts pop up") == "speaking-up-on-its-own"
    assert name("the morning English lesson") == "learning"
    assert name("how do phone calls work") == "phone-calls"
    assert name("price book patterns sandbox") == "the-price-book-pattern-learning-and-trained-models"
    assert name("how is your day") is None


def test_the_diagrams_are_safe_graphviz_and_valid_mermaid(features):
    for f in features:
        g = cf.dot(f)
        assert g.startswith("digraph feature {") and g.rstrip().endswith("}")
        assert not makers.UNSAFE_DOT.search(g) and len(g) < 40_000
        assert g.count("{") == g.count("}") and all(m["file"] in g for m in f["modules"])
        m = cf.mermaid(f)
        assert m.startswith("flowchart LR") and m.count("subgraph") == m.count("\n    end")
    whole = cf.system_dot(features)
    assert whole.startswith("digraph buddy {") and not makers.UNSAFE_DOT.search(whole)
    assert whole.count("{") == whole.count("}") and "Claude" in whole and "Price book" in whole
    assert not re.search(r'-> "f_[a-z0-9-]+"', "\n".join(
        ln for ln in whole.splitlines() if any(s not in {f["slug"] for f in features}
                                               for s in re.findall(r'"f_([a-z0-9-]+)"', ln))))


def test_the_pages_are_written_one_per_feature_with_an_overview(tmp_path, features):
    files = cf.write_docs(str(tmp_path))
    assert files[0] == "README.md" and len(files) == len(features) + 1
    readme = (tmp_path / "README.md").read_text()
    assert "## The whole system" in readme and "sequenceDiagram" in readme and "## What learns from what" in readme
    assert all(f"({name})" in readme for name in files[1:])
    page = next(p for p in tmp_path.iterdir() if "shopping-and-deals" in p.name).read_text()
    assert page.startswith("# Shopping and deals") and "```mermaid" in page and "### dealcollector.py" in page
    assert "| `daily_deals` |" in page and "## How it connects" in page


def test_buddy_shows_the_design_with_the_diagram_in_the_chat(tmp_path, monkeypatch):
    memory = Memory(tmp_path / "buddy.db")
    ConnectorHub(memory)
    drawn = []
    monkeypatch.setattr(makers, "render_diagram", lambda dot: drawn.append(dot) or b"\\x89PNG fake")
    monkeypatch.setattr(makers, "_show", lambda card: "c1")
    got = json.loads(cf.show(memory, "how does the deals collector work, with a diagram"))
    assert got["feature"] == "Shopping and deals" and "[dealcollector.py]" in got["design"]
    assert got["diagram"].startswith("It's in the chat") and got["card"] == "c1" and drawn[0].startswith("digraph feature")
    whole = json.loads(cf.show(memory, "show me the whole architecture of Buddy"))
    assert whole["architecture"] == "Buddy: the whole system" and len(whole["features"]) == len(codedocs.COMPONENTS)
    assert drawn[1].startswith("digraph buddy")
    assert "No feature matches" in json.loads(cf.show(memory, "zzzz qqqq"))["note"]


def test_without_graphviz_the_flow_is_given_in_words(tmp_path, monkeypatch):
    from buddy.tools import ToolError
    memory = Memory(tmp_path / "buddy.db")
    ConnectorHub(memory)

    def no_dot(dot):
        raise ToolError("Graphviz isn't installed on the server (sudo apt install graphviz).")
    monkeypatch.setattr(makers, "render_diagram", no_dot)
    got = json.loads(cf.show(memory, "phone calls"))
    assert "couldn't draw it" in got["diagram"] and got["flow"].startswith("You -> tools (") and "calls.py" in got["flow"]


def test_a_question_about_buddy_itself_goes_to_the_live_code_not_the_library(tmp_path):
    """Live: 'show me your architecture' was answered from an old document in the library."""
    from buddy import thinking
    from buddy.brain import Brain
    memory = Memory(tmp_path / "buddy.db")
    ConnectorHub(memory)
    brain = Brain(memory)
    for q in ('"show me your architecture".', "show me your architecture", "how does the deals collector work?",
              "explain the technical design of the alerts", "what is Buddy's architecture",
              "how do you work", "diagram of the investing signals", "how does your practice book work"):
        prep = thinking.prepare(brain, q)
        assert "buddy_code tool" in prep.pack and not prep.direct and not prep.lookup, q
    for q in ("when does my passport expire?", "what did I spend at Costco", "how does my insurance work",
              "show me my flights"):
        assert "buddy_code" not in thinking.prepare(brain, q).pack, q
