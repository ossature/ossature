import pytest
from conftest import make_smd

from ossature.audit.graph import _topological_levels


class TestTopologicalLevels:
    def test_levels_follow_dependencies(self):
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        assert _topological_levels(smds) == [["AUTH"], ["API"]]

    def test_cycle_raises(self):
        smds = [make_smd("A", depends=["B"]), make_smd("B", depends=["A"])]
        with pytest.raises(ValueError, match="Dependency cycle"):
            _topological_levels(smds)
