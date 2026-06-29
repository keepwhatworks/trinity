"""K-NN ranker: heuristic + embedding-based advisory upgrade."""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Ranker
from .heuristic import HeuristicRanker
from .types import RoutingContext, RoutingDecision


class KnnRanker(Ranker):
    """Routes using heuristic baseline + k-NN advisory upgrade.

    Flow:
      1. Get heuristic recommendation from HeuristicRanker
      2. Query k-NN advisor (embedding-based)
      3. Enhance decision with k-NN evidence and council signals
      4. Fall back to heuristic if k-NN unavailable

    Upgrade rules:
      - Can promote recommendation → council if neighbors agree
      - Never downgrades council → recommendation
      - Adds evidence from nearest neighbors
      - Annotates with k-NN metadata
      - Logs advisory call to analytics
    """

    def __init__(self):
        self._heuristic = HeuristicRanker()

    def advise(self, context: RoutingContext) -> RoutingDecision:
        """Advise with heuristic baseline + k-NN upgrade."""
        # Get heuristic decision
        decision = self._heuristic.advise(context)

        # Try to upgrade with k-NN (gracefully degrades if unavailable)
        try:
            from ..knn_advisor import advise as knn_advise
        except ImportError:
            return decision

        # Only attempt k-NN if we have a prompt to embed
        task_text = context.task_text or ""
        if not task_text.strip():
            self._log_advisory_miss(context, decision)
            return decision

        advice = knn_advise(task_text, context.current_provider)
        if advice is None:
            self._log_advisory_miss(context, decision)
            return decision

        # Enhance decision with k-NN advice
        new_decision = self._upgrade_decision(decision, advice, context)
        self._log_advisory_success(context, decision, new_decision, advice)

        return new_decision

    def _upgrade_decision(
        self,
        decision: RoutingDecision,
        advice,
        context: RoutingContext,
    ) -> RoutingDecision:
        """Enhance decision with k-NN advice."""
        new_evidence = list(decision.evidence)

        # Add k-NN evidence from neighbors
        if advice.evidence:
            new_evidence.extend(advice.evidence)

        # Upgrade to council if neighbors agree (never downgrade)
        new_needs_council = decision.needs_council
        new_top_k = list(decision.top_k)
        reason_suffix = ""

        if advice.should_council and not decision.needs_council:
            new_needs_council = True
            reason_suffix = (
                f" [k-NN: {advice.council_confidence:.0%} neighbor agreement suggests council]"
            )
            # Add council members if not present
            if not new_top_k and advice.top2_providers:
                new_top_k = advice.top2_providers[:2]

        # Suggest reroute if similar session in different provider
        if (
            advice.reroute_provider
            and advice.reroute_similarity > 0.7
            and advice.council_confidence > 0.5
        ):
            reason_suffix += (
                f" [k-NN: similar session in {advice.reroute_provider} "
                f"(sim={advice.reroute_similarity:.2f})]"
            )

        # Return enhanced decision
        return RoutingDecision(
            recommended_provider=decision.recommended_provider,
            top_k=new_top_k,
            needs_council=new_needs_council,
            confidence=decision.confidence,  # Keep heuristic confidence
            evidence=new_evidence,
            backend="knn",
            metadata={
                **(decision.metadata or {}),
                "knn_method": "embedding_knn",
                "knn_neighbor_count": advice.neighbor_count,
                "knn_council_confidence": advice.council_confidence,
                "reason_suffix": reason_suffix,
            },
        )

    def _log_advisory_miss(self, context: RoutingContext, decision: RoutingDecision) -> None:
        """Log when k-NN advisor is unavailable."""
        try:
            from ..knn_analytics import AdvisoryEvent, log_advisory_event

            event = AdvisoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=context.session_id,
                provider=context.current_provider,
                task_type=context.task_type,
                prompt_len=len(context.task_text or ""),
                knn_available=False,
                neighbor_count=0,
                council_confidence=0.0,
                should_council=False,
                reroute_provider=None,
                reroute_similarity=0.0,
                top2_providers=[],
                evidence_count=len(decision.evidence),
                heuristic_mode="recommendation" if not decision.needs_council else "council",
                final_mode="recommendation" if not decision.needs_council else "council",
                was_upgraded=False,
                recommended_provider=decision.recommended_provider or "",
            )
            log_advisory_event(event)
        except Exception:
            pass  # Analytics must never break routing

    def _log_advisory_success(
        self,
        context: RoutingContext,
        heuristic_decision: RoutingDecision,
        knn_decision: RoutingDecision,
        advice,
    ) -> None:
        """Log when k-NN advisor enhances the decision."""
        try:
            from ..knn_analytics import AdvisoryEvent, log_advisory_event

            was_upgraded = (
                not heuristic_decision.needs_council and knn_decision.needs_council
            )

            event = AdvisoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=context.session_id,
                provider=context.current_provider,
                task_type=context.task_type,
                prompt_len=len(context.task_text or ""),
                knn_available=True,
                neighbor_count=advice.neighbor_count,
                council_confidence=advice.council_confidence,
                should_council=advice.should_council,
                reroute_provider=advice.reroute_provider,
                reroute_similarity=advice.reroute_similarity,
                top2_providers=advice.top2_providers,
                evidence_count=len(knn_decision.evidence),
                heuristic_mode="recommendation"
                if not heuristic_decision.needs_council
                else "council",
                final_mode="recommendation" if not knn_decision.needs_council else "council",
                was_upgraded=was_upgraded,
                recommended_provider=knn_decision.recommended_provider or "",
            )
            log_advisory_event(event)
        except Exception:
            pass  # Analytics must never break routing
