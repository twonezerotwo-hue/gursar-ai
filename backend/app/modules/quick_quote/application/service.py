from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.contracts.v1.customer_understanding import (
    FACADE_SERVICE_CONSTRAINT,
    ConversationActionV1,
    CustomerUnderstandingContextV1,
    ExistingJobContextV1,
    JobUnderstandingV1,
    LocationV1,
    QuantityV1,
)
from app.contracts.v1.quick_quote import ChatTurnResponseV1, QuickQuoteV1
from app.core.persistence import append_audit, append_outbox
from app.modules.construction_chat.domain.ports import (
    ChatMessage,
    ConstructionChatClassification,
    ConstructionChatContext,
    ConstructionChatProvider,
    ConstructionChatProviderError,
)
from app.modules.conversation.domain.models import CustomerJob, MessageRole
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.conversation_intelligence.application.service import StructuredIntentRouter
from app.modules.conversation_intelligence.domain.models import ConstructionIntent, ProjectContext
from app.modules.conversation_intelligence.infrastructure.repository import ProjectContextRepository
from app.modules.customer_understanding.application.service import CustomerUnderstandingService
from app.modules.customer_understanding.infrastructure.location import fold_text
from app.modules.document_intelligence.domain.ports import DocumentAnalysisRepository
from app.modules.estimation.application.chat import (
    is_building_estimation_intent,
    parse_building_estimation_request,
    render_estimation_intake,
    render_estimation_response,
)
from app.modules.estimation.application.service import EstimationService
from app.modules.legal_knowledge.domain.ports import LegalKnowledgeSearch
from app.modules.planning.application.service import ConstructionSchedulingService
from app.modules.planning.domain.models import PlanningPriority, PlanningRequest, ScheduleResult
from app.modules.planning.infrastructure.repository import ProductivityRepository
from app.modules.pricing.application.service import PriceResolver
from app.modules.pricing.domain.location import LocationNormalizer
from app.modules.quick_quote.application.presentation import (
    format_number,
    render_quote_response,
    unit_label,
)
from app.modules.quick_quote.domain.models import calculate_total
from app.modules.quick_quote.infrastructure.repository import QuickQuoteRepository
from app.modules.service_catalog.infrastructure.repository import ServiceCatalogRepository
from app.modules.work_package.domain.models import WorkPackage
from app.modules.work_package.infrastructure.repository import WorkPackageRepository

logger = structlog.get_logger(__name__)
MONEY_PATTERN = re.compile(
    r"(?:\d[\d.,]*\s*(?:TL|TRY|EUR|USD|₺)|(?:TL|TRY|EUR|USD|₺)\s*\d)",
    re.IGNORECASE,
)
INTERNAL_OUTPUT_PATTERN = re.compile(
    r"PRICE_BOOK_RESULT|dahili teklif bağlam|verified_commercial_result_available|scope_summary|"
    r"clarification_question|PROJECT_DOCUMENT_CONTEXT|document_context|system prompt|"
    r"JSON (?:şeması|schema)",
    re.IGNORECASE,
)
LEGAL_QUERY_MARKERS = (
    "kanun",
    "mevzuat",
    "yonetmelik",
    "sartname",
    "resmi gazete",
    "teblig",
    "tse",
    "standart",
    "tbdy",
    "deprem",
    "ruhsat",
    "imar",
    "madde",
    "poz",
    "rayic",
    "ifc",
    "bim",
    "yfk",
    "yangin",
    "enerji performans",
    "erisilebilir",
)
DOCUMENT_QUERY_MARKERS = (
    "dosya",
    "proje",
    "pafta",
    "cizim",
    "çizim",
    "ifc",
    "dxf",
    "yukledig",
    "yüklediğ",
    "katman",
    "modelde",
)


class ConversationNotFoundError(LookupError):
    pass


class QuickQuoteService:
    def __init__(
        self,
        *,
        session: Session,
        conversations: ConversationRepository,
        understanding: CustomerUnderstandingService,
        services: ServiceCatalogRepository,
        work_packages: WorkPackageRepository,
        price_resolver: PriceResolver,
        quotes: QuickQuoteRepository,
        location_normalizer: LocationNormalizer,
        default_location_code: str,
        construction_chat_provider: ConstructionChatProvider | None = None,
        legal_knowledge: LegalKnowledgeSearch | None = None,
        document_analyses: DocumentAnalysisRepository | None = None,
        estimation: EstimationService | None = None,
    ) -> None:
        self._session = session
        self._conversations = conversations
        self._understanding = understanding
        self._services = services
        self._work_packages = work_packages
        self._price_resolver = price_resolver
        self._quotes = quotes
        self._location_normalizer = location_normalizer
        self._default_location_code = default_location_code
        self._construction_chat_provider = construction_chat_provider
        self._legal_knowledge = legal_knowledge
        self._document_analyses = document_analyses
        self._estimation = estimation
        self._intent_router = StructuredIntentRouter()
        self._project_contexts = ProjectContextRepository(session)
        productivity = ProductivityRepository(session).active_catalog()
        self._scheduling = ConstructionSchedulingService(productivity=productivity or None)

    def handle_customer_message(
        self,
        *,
        conversation_id: str,
        text: str,
    ) -> ChatTurnResponseV1:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

        message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=text,
        )
        current_project_context = self._project_contexts.get(conversation_id)
        routed_turn = self._intent_router.route(text, current_project_context)
        project_context = self._project_contexts.merge(conversation_id, routed_turn.updates)

        def enriched(response: ChatTurnResponseV1) -> ChatTurnResponseV1:
            return response.model_copy(
                update={
                    "intent": routed_turn.intent.value,
                    "confidence": routed_turn.confidence,
                    "assumptions": list(routed_turn.assumptions),
                }
            )

        if routed_turn.intent in {
            ConstructionIntent.SITE_SCHEDULE,
            ConstructionIntent.CREW_PLANNING,
        } or (
            project_context.goal == "site_schedule" and self._planning_priority(text) is not None
        ):
            return self._handle_planning_request(
                conversation_id=conversation_id,
                text=text,
                context=project_context,
                routed_intent=routed_turn.intent,
                confidence=routed_turn.confidence,
                inherited_assumptions=routed_turn.assumptions,
            )
        existing_jobs = self._conversations.list_jobs(conversation_id)
        estimation_input = parse_building_estimation_request(text)
        history = self._conversations.list_messages(conversation_id)
        estimation_intake_active = self._estimation_intake_active(history)
        estimation_context = "\n".join(
            entry.content for entry in history[-10:] if entry.role == MessageRole.USER
        )
        if estimation_input is None and estimation_intake_active:
            estimation_input = parse_building_estimation_request(estimation_context)
        if estimation_input is not None and self._estimation is not None:
            estimation = self._estimation.estimate(
                estimation_input,
                conversation_id=conversation_id,
            )
            customer_text = render_estimation_response(estimation)
            assistant_message = self._conversations.add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=customer_text,
            )
            append_outbox(
                self._session,
                event_type="conversation.estimation_answered.v1",
                aggregate_id=conversation_id,
                payload={"request_id": estimation.request_id},
            )
            return enriched(
                ChatTurnResponseV1(
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message.id,
                    assistant_text=customer_text,
                    quotes=[],
                    tools_used=["ESTIMATION_ENGINE"],
                )
            )
        if is_building_estimation_intent(text) or estimation_intake_active:
            customer_text = render_estimation_intake(estimation_context)
            assistant_message = self._conversations.add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=customer_text,
            )
            return enriched(
                ChatTurnResponseV1(
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message.id,
                    assistant_text=customer_text,
                    quotes=[],
                )
            )
        if self._is_project_reporting_or_scheduling_turn(text):
            return enriched(
                self._answer_construction_question(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
                )
            )
        if self._is_rough_construction_turn(text, existing_jobs):
            return enriched(
                self._handle_rough_construction(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
                    message_id=message.id,
                    text=text,
                    existing_jobs=existing_jobs,
                )
            )
        if self._is_fine_construction_turn(text):
            return enriched(
                self._handle_fine_construction(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
                )
            )
        if self._is_total_summary_request(text):
            return enriched(
                self._handle_total_summary(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
                )
            )

        chat_classification = (
            None
            if self._is_deterministic_quote_turn(text, existing_jobs)
            else self._classify_chat_message(
                conversation_id=conversation_id,
                locale=conversation.locale,
            )
        )
        if chat_classification is not None:
            if not chat_classification.in_scope:
                return enriched(
                    self._save_scope_refusal(
                        conversation_id=conversation_id,
                        locale=conversation.locale,
                    )
                )
            if not chat_classification.quote_intent:
                return enriched(
                    self._answer_construction_question(
                        conversation_id=conversation_id,
                        locale=conversation.locale,
                    )
                )
        context = CustomerUnderstandingContextV1(
            conversation_id=conversation_id,
            message_id=message.id,
            locale=conversation.locale,
            jobs=[
                ExistingJobContextV1(
                    job_id=job.id,
                    service_code=job.service_code,
                    quantity=job.quantity,
                    unit=job.unit,
                    location_code=job.location_code,
                    location_label=job.location_label,
                )
                for job in existing_jobs
            ],
        )
        understanding = self._understanding.analyze(message=text, context=context)
        if len(understanding.jobs) == 1:
            understood = understanding.jobs[0]
            if understood.action == ConversationActionV1.TOTAL:
                return enriched(
                    self._handle_total_summary(
                        conversation_id=conversation_id,
                        locale=understanding.detected_language or conversation.locale,
                    )
                )
            if understood.action == ConversationActionV1.REMOVE:
                return enriched(
                    self._handle_remove_job(
                        conversation_id=conversation_id,
                        locale=understanding.detected_language or conversation.locale,
                        understood=understood,
                        existing_jobs=existing_jobs,
                    )
                )
            if self._needs_job_clarification(understood):
                return enriched(
                    self._handle_job_clarification(
                        conversation_id=conversation_id,
                        locale=understanding.detected_language or conversation.locale,
                    )
                )

        quick_quotes = [
            self._process_understood_job(
                conversation_id=conversation_id,
                locale=understanding.detected_language or conversation.locale,
                understood=understood,
                existing_jobs=existing_jobs,
                is_multi_job=len(understanding.jobs) > 1,
            )
            for understood in understanding.jobs
        ]
        deterministic_text = render_quote_response(
            quick_quotes,
            understanding.detected_language or conversation.locale,
        )
        customer_text = self._render_customer_response(
            conversation_id=conversation_id,
            locale=understanding.detected_language or conversation.locale,
            quotes=quick_quotes,
            fallback=deterministic_text,
        )
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="customer_understanding.updated.v1",
            aggregate_id=conversation_id,
            payload={
                "conversation_id": conversation_id,
                "message_id": message.id,
                "snapshot_schema": understanding.schema_version,
            },
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=quick_quotes,
            intent=routed_turn.intent.value,
            confidence=routed_turn.confidence,
            assumptions=list(routed_turn.assumptions),
        )

    def _handle_planning_request(
        self,
        *,
        conversation_id: str,
        text: str,
        context: ProjectContext,
        routed_intent: ConstructionIntent,
        confidence: float,
        inherited_assumptions: tuple[str, ...],
    ) -> ChatTurnResponseV1:
        missing = []
        if context.footprint_m2 is None and context.gross_area_m2 is None:
            missing.append("oturum veya toplam inşaat alanı")
        if context.floor_count is None:
            missing.append("kat sayısı")
        if missing:
            question = f"Programı hazırlayabilirim. Önce {missing[0]} nedir?"
            assistant = self._conversations.add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=question,
            )
            return ChatTurnResponseV1(
                conversation_id=conversation_id,
                assistant_message_id=assistant.id,
                assistant_text=question,
                intent=ConstructionIntent.SITE_SCHEDULE.value,
                confidence=confidence,
                quick_replies=[],
                assumptions=list(inherited_assumptions),
            )

        assert context.floor_count is not None
        gross_area = context.gross_area_m2 or (
            (context.footprint_m2 or Decimal("0")) * Decimal(context.floor_count)
        )
        priority = self._planning_priority(text)
        if priority is None and context.facts.get("crew_planning_delegated") is True:
            priority = PlanningPriority.BALANCED
        if priority is None:
            question = "Tamam, proje bağlamını kaydettim. Programı hangi öncelikle hazırlayalım?"
            assistant = self._conversations.add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=question,
            )
            return ChatTurnResponseV1(
                conversation_id=conversation_id,
                assistant_message_id=assistant.id,
                assistant_text=question,
                intent=ConstructionIntent.SITE_SCHEDULE.value,
                tools_used=["PROJECT_CONTEXT"],
                assumptions=list(inherited_assumptions),
                confidence=confidence,
                quick_replies=[
                    {"label": "En hızlı", "value": "fast"},
                    {"label": "En ekonomik", "value": "economic"},
                    {"label": "Dengeli", "value": "balanced"},
                ],
            )

        result = self._scheduling.create(
            PlanningRequest(
                gross_area_m2=gross_area,
                floor_count=context.floor_count,
                stage=context.stage or "FOUNDATION_COMPLETED",
                project_type=context.project_type or "RESIDENTIAL",
                priority=priority,
                available_crew=context.available_crew,
            )
        )
        plan_id = self._project_contexts.save_plan(
            conversation_id,
            context.version,
            priority,
            result,
        )
        customer_text = self._render_schedule(result, priority)
        assistant = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="conversation.schedule_created.v1",
            aggregate_id=plan_id,
            payload={"conversation_id": conversation_id, "context_version": context.version},
        )
        planning = {
            "plan_id": plan_id,
            "priority": priority.value,
            "duration_days": str(result.duration_days),
            "peak_crew": result.peak_crew,
            "critical_path": list(result.critical_path),
            "critical_paths": [list(path) for path in result.critical_paths],
            "activities": [
                {
                    "id": item.activity.id,
                    "wbs_code": item.activity.wbs_code,
                    "name": item.activity.name,
                    "duration_days": str(item.activity.duration_days),
                    "crew_size": item.activity.crew_size,
                    "early_start": str(item.early_start),
                    "early_finish": str(item.early_finish),
                    "late_start": str(item.late_start),
                    "late_finish": str(item.late_finish),
                    "total_float": str(item.total_float),
                    "free_float": str(item.free_float),
                    "critical": item.critical,
                }
                for item in result.activities
            ],
        }
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant.id,
            assistant_text=customer_text,
            intent=(
                routed_intent.value
                if routed_intent != ConstructionIntent.UNKNOWN
                else ConstructionIntent.SITE_SCHEDULE.value
            ),
            tools_used=["PROJECT_CONTEXT", "PRODUCTIVITY_ENGINE", "CPM_ENGINE", "CREW_PLANNER"],
            assumptions=list(result.assumptions),
            confidence=confidence,
            planning=planning,
            quick_replies=[
                {"label": "Aktiviteleri göster", "value": "show_activities"},
                {"label": "Ekibi optimize et", "value": "optimize_crew"},
            ],
        )

    @staticmethod
    def _planning_priority(text: str) -> PlanningPriority | None:
        folded = fold_text(text)
        if any(marker in folded for marker in ("en hizli", "en hızlı", "fast", "hizli", "hızlı")):
            return PlanningPriority.FAST
        if any(
            marker in folded
            for marker in ("ekonomik", "economic", "dusuk maliyet", "düşük maliyet")
        ):
            return PlanningPriority.ECONOMIC
        if any(marker in folded for marker in ("dengeli", "balanced")):
            return PlanningPriority.BALANCED
        return None

    @staticmethod
    def _render_schedule(result: ScheduleResult, priority: PlanningPriority) -> str:
        priority_label = {
            PlanningPriority.FAST: "hızlı",
            PlanningPriority.ECONOMIC: "ekonomik",
            PlanningPriority.BALANCED: "dengeli",
        }[priority]
        critical_names = [
            item.activity.name
            for item in result.activities
            if item.activity.id in result.critical_path
        ]
        critical = " → ".join(critical_names[:4])
        return (
            f"{priority_label.capitalize()} senaryoda yaklaşık {result.duration_days} "
            "takvim günü ve "
            f"en yoğun dönemde {result.peak_crew} kişilik ekip öngörülüyor. Kritik akış: "
            f"{critical}. Bu, klasik konut varsayımlı ön programdır; onaylı proje ve saha "
            "kısıtlarıyla kesinleştirilmelidir."
        )

    @staticmethod
    def _estimation_intake_active(history: list[Any]) -> bool:
        last_assistant = next(
            (entry for entry in reversed(history) if entry.role == MessageRole.ASSISTANT),
            None,
        )
        return bool(
            last_assistant
            and last_assistant.content.startswith("Metrajı hemen yaklaşık olarak hesaplayabilirim.")
        )

    @staticmethod
    def _needs_job_clarification(understood: JobUnderstandingV1) -> bool:
        return (
            understood.action == ConversationActionV1.CLARIFY
            and understood.service_candidate is None
            and understood.job_reference is None
            and not understood.constraints
        )

    def _handle_job_clarification(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ChatTurnResponseV1:
        customer_text = (
            "Bu metrajı hangi iş kalemine uygulayacağımı belirtir misiniz? "
            "Örneğin demir, kalıp veya duvar işçiliği."
            if locale == "tr"
            else "Which work item should this quantity apply to? For example, reinforcement, "
            "formwork, or masonry labor."
        )
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    def _handle_remove_job(
        self,
        *,
        conversation_id: str,
        locale: str,
        understood: JobUnderstandingV1,
        existing_jobs: list[CustomerJob],
    ) -> ChatTurnResponseV1:
        target = self._target_job(understood, existing_jobs, is_multi_job=False)
        if target is None:
            customer_text = (
                "Silinecek iş kalemini bulamadım. Demir, kalıp veya duvar gibi kalemi belirtin."
                if locale == "tr"
                else "I could not find the item to remove. Please name the work item."
            )
        else:
            removed = self._conversations.remove_job(
                conversation_id=conversation_id,
                job_id=target.id,
            )
            service = (
                self._services.get_active(target.service_code) if target.service_code else None
            )
            label = service.label(locale) if service else target.service_code or "İş kalemi"
            customer_text = (
                f"{label} hesap defterinden çıkarıldı."
                if locale == "tr"
                else f"{label} was removed from the estimate ledger."
            )
            if removed is not None:
                append_outbox(
                    self._session,
                    event_type="job.removed.v1",
                    aggregate_id=removed.id,
                    payload={"conversation_id": conversation_id, "job_id": removed.id},
                )

        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    @staticmethod
    def _is_deterministic_quote_turn(text: str, existing_jobs: list[CustomerJob]) -> bool:
        folded = fold_text(text)
        service_markers = ("demir", "donati", "kalip", "duvar")
        has_service = any(marker in folded for marker in service_markers)
        commercial_markers = (
            "fiyat",
            "birim",
            "iscilik",
            "metraj",
            "metrekare",
            "m2",
            "ne kadar",
            "kacti",
            "revize",
            "guncelle",
            "degistir",
        )
        if has_service and (
            any(marker in folded for marker in commercial_markers)
            or bool(re.search(r"\d", folded))
            or len(folded.split()) <= 3
        ):
            return True

        has_pending_job = any(job.quantity is None for job in existing_jobs)
        return has_pending_job and (
            bool(re.search(r"\d", folded))
            or any(marker in folded for marker in ("malzemeli", "malzemesiz"))
        )

    @staticmethod
    def _is_project_reporting_or_scheduling_turn(text: str) -> bool:
        folded = fold_text(text)
        return any(
            marker in folded
            for marker in (
                "bitti",
                "tamamlandi",
                "eksik",
                "kusur",
                "raporla",
                "raporlan",
                "is program",
                "programla",
                "takvim",
                "kalan isler",
                "ilerleme",
                "punch list",
            )
        )

    @staticmethod
    def _is_rough_construction_turn(text: str, existing_jobs: list[CustomerJob]) -> bool:
        folded = fold_text(text)
        if "kaba insaat" in folded:
            return True
        has_pending_rough_job = any(
            job.service_code == "rough_construction.turnkey"
            and (not job.scope_state or job.quantity is None)
            for job in existing_jobs
        )
        if not has_pending_rough_job:
            return False
        return any(
            marker in folded
            for marker in (
                "malzemeli",
                "malzemesiz",
                "malzeme dahil",
                "malzeme haric",
                "sadece iscilik",
                "m2",
                "metrekare",
            )
        ) or bool(re.search(r"\d", folded))

    @staticmethod
    def _is_fine_construction_turn(text: str) -> bool:
        return "ince insaat" in fold_text(text)

    def _handle_fine_construction(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ChatTurnResponseV1:
        customer_text = (
            "**İnce İnşaat** şablonu hazır. İnce inşaat kalemlerinin fiyatları henüz "
            "tanımlanmadığı için şu anda hesaplama yapmıyorum. Fiyatları eklediğinizde "
            "kalem kalem metraj ve toplam hakediş hesabı burada çalışacak."
            if locale == "tr"
            else "The fine-construction template is ready. Pricing will be enabled after "
            "its item rates are configured."
        )
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="conversation.fine_construction_template_selected.v1",
            aggregate_id=conversation_id,
            payload={"conversation_id": conversation_id},
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    @staticmethod
    def _is_total_summary_request(text: str) -> bool:
        folded = fold_text(text)
        return (
            folded.strip(" ?.!") in {"toplam", "toplami"}
            or "genel toplam" in folded
            or "toplam metraj" in folded
            or "hepsi ne kadar" in folded
            or (
                "toplam" in folded
                and any(
                    marker in folded
                    for marker in ("maliyet", "ne etti", "ne kadar", "tutar", "kac")
                )
            )
        )

    def _handle_total_summary(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ChatTurnResponseV1:
        stored_quotes = self._quotes.list_latest_by_conversation(conversation_id)
        ordered_jobs = self._conversations.list_jobs(conversation_id)
        jobs = {job.id: job for job in ordered_jobs}
        job_order = {job.id: index for index, job in enumerate(ordered_jobs)}
        stored_quotes.sort(key=lambda quote: job_order.get(quote.job_id, len(job_order)))
        lines: list[str] = []
        grand_total = Decimal(0)
        missing_quantity = False
        for stored in stored_quotes:
            service = self._services.get_active(stored.service_code)
            job = jobs.get(stored.job_id)
            label = service.label(locale) if service else stored.service_code
            unit = (job.unit if job else None) or "m2"
            if stored.total_price is not None and stored.quantity is not None:
                grand_total += stored.total_price
                lines.append(
                    f"- {label}: {format_number(stored.quantity, locale)} "
                    f"{unit_label(unit, locale)} × "
                    f"{format_number(stored.unit_price, locale, money=True)} TL = "
                    f"{format_number(stored.total_price, locale, money=True)} TL"
                )
            else:
                missing_quantity = True
                lines.append(
                    f"- {label}: {format_number(stored.unit_price, locale, money=True)} "
                    f"TL/{unit_label(unit, locale)} — metraj bekleniyor"
                )

        if not lines:
            customer_text = "Henüz toplanacak bir iş kalemi bulunmuyor."
        else:
            customer_text = (
                "Güncel hakediş özeti:\n"
                + "\n".join(lines)
                + "\n\n**Genel toplam: "
                + f"{format_number(grand_total, locale, money=True)} TL + KDV**"
            )
            if missing_quantity:
                customer_text += "\n\nMetrajı girilmemiş kalemler genel toplama dahil değildir."

        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="conversation.quote_summary_answered.v1",
            aggregate_id=conversation_id,
            payload={"conversation_id": conversation_id},
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    def _handle_rough_construction(
        self,
        *,
        conversation_id: str,
        locale: str,
        message_id: str,
        text: str,
        existing_jobs: list[CustomerJob],
    ) -> ChatTurnResponseV1:
        context = CustomerUnderstandingContextV1(
            conversation_id=conversation_id,
            message_id=message_id,
            locale=locale,
            jobs=[
                ExistingJobContextV1(
                    job_id=job.id,
                    service_code=job.service_code,
                    quantity=job.quantity,
                    unit=job.unit,
                    location_code=job.location_code,
                    location_label=job.location_label,
                )
                for job in existing_jobs
            ],
        )
        understanding = self._understanding.analyze(message=text, context=context)
        quantity_understanding = next(
            (job.quantity for job in understanding.jobs if job.quantity is not None),
            None,
        )
        location_understanding = next(
            (job.location for job in understanding.jobs if job.location is not None),
            None,
        )
        evidence = [
            item.model_dump(mode="json") for job in understanding.jobs for item in job.evidence
        ]
        pending = next(
            (
                job
                for job in reversed(existing_jobs)
                if job.service_code == "rough_construction.turnkey"
                and (not job.scope_state or job.quantity is None)
            ),
            None,
        )
        quantity = quantity_understanding.value if quantity_understanding else None
        unit = quantity_understanding.unit if quantity_understanding else None
        if quantity is None and pending is not None:
            quantity = pending.quantity
            unit = pending.unit

        explicit_location = None
        if location_understanding:
            explicit_location = location_understanding.code or location_understanding.display_name
        inherited_location = pending.location_code if pending else None
        location = self._location_normalizer.normalize(
            explicit_location or inherited_location,
            self._default_location_code,
        )
        folded = fold_text(text)
        scope_mode = self._rough_scope_mode(folded) or self._rough_scope_mode_from_job(pending)
        if scope_mode is None:
            rough_job = self._conversations.save_job(
                conversation_id=conversation_id,
                job_id=pending.id if pending else None,
                service_code="rough_construction.turnkey",
                quantity=quantity,
                unit=unit or "m2",
                location_code=location.code,
                location_label=location.display_name,
                scope_state=[],
                provenance=evidence,
            )
            quantity_question = "" if quantity is not None else " Toplam metraj kaç m²?"
            clarification = (
                "Kaba inşaatı doğru hesaplamak için önce kapsamı netleştirelim: "
                f"**malzemeli mi, malzemesiz mi?**{quantity_question}"
            )
            quote = QuickQuoteV1(
                job_id=rough_job.id,
                service_code="rough_construction.turnkey",
                service_label="Kaba İnşaat",
                location_code=location.code,
                location_label=location.display_name,
                quantity=quantity,
                unit=unit or "m2",
                status="SERVICE_CLARIFICATION_REQUIRED",
                scope_summary="Kapsam seçimi bekleniyor.",
                clarification_question=clarification,
                customer_text="",
            )
            return self._save_rough_response(
                conversation_id=conversation_id,
                message_id=message_id,
                understanding_schema=understanding.schema_version,
                text=clarification,
                quotes=[quote],
            )

        rough_job = self._conversations.save_job(
            conversation_id=conversation_id,
            job_id=pending.id if pending else None,
            service_code="rough_construction.turnkey",
            quantity=quantity,
            unit=unit or "m2",
            location_code=location.code,
            location_label=location.display_name,
            scope_state=[
                {
                    "item_code": "rough.material_scope",
                    "label": (
                        "Kalıp malzemesi dahil"
                        if scope_mode == "FORMWORK_MATERIAL_INCLUDED"
                        else "Malzemeler hariç"
                    ),
                    "state": "CONFIRMED_BY_CUSTOMER",
                    "supply_by": "EITHER",
                    "pricing_role": "SCOPE_DECISION",
                }
            ],
            provenance=evidence,
        )

        component_codes = [
            "reinforcement.labor",
            (
                "formwork.labor_material"
                if scope_mode == "FORMWORK_MATERIAL_INCLUDED"
                else "formwork.labor"
            ),
            "masonry.labor",
        ]
        component_quantity = QuantityV1(value=quantity, unit=unit or "m2") if quantity else None
        component_location = LocationV1(
            code=location.code,
            display_name=location.display_name,
        )
        component_quotes = [
            self._process_understood_job(
                conversation_id=conversation_id,
                locale=locale,
                understood=JobUnderstandingV1(
                    action=ConversationActionV1.UPDATE,
                    service_candidate=service_code,
                    service_confidence=1,
                    quantity=component_quantity,
                    location=component_location,
                ),
                existing_jobs=existing_jobs,
                is_multi_job=False,
            )
            for service_code in component_codes
        ]
        response_text = self._render_rough_construction_response(
            component_quotes,
            locale=locale,
            material_included=scope_mode == "FORMWORK_MATERIAL_INCLUDED",
        )
        return self._save_rough_response(
            conversation_id=conversation_id,
            message_id=message_id,
            understanding_schema=understanding.schema_version,
            text=response_text,
            quotes=component_quotes,
        )

    @staticmethod
    def _rough_scope_mode(folded: str) -> str | None:
        if any(marker in folded for marker in ("malzemesiz", "malzeme haric", "sadece iscilik")):
            return "LABOR_ONLY"
        if any(marker in folded for marker in ("malzemeli", "malzeme dahil")):
            return "FORMWORK_MATERIAL_INCLUDED"
        return None

    @staticmethod
    def _rough_scope_mode_from_job(job: CustomerJob | None) -> str | None:
        if job is None:
            return None
        for item in job.scope_state:
            if item.get("item_code") != "rough.material_scope":
                continue
            label = fold_text(str(item.get("label", "")))
            return (
                "FORMWORK_MATERIAL_INCLUDED"
                if "dahil" in label
                else "LABOR_ONLY"
                if "haric" in label
                else None
            )
        return None

    @staticmethod
    def _render_rough_construction_response(
        quotes: list[QuickQuoteV1],
        *,
        locale: str,
        material_included: bool,
    ) -> str:
        unit_total = sum((quote.unit_price or Decimal(0) for quote in quotes), Decimal(0))
        amount_total = sum((quote.total_price or Decimal(0) for quote in quotes), Decimal(0))
        lines = [
            f"- {quote.service_label}: "
            f"{format_number(quote.unit_price or Decimal(0), locale, money=True)} TL/m²"
            for quote in quotes
        ]
        if quotes[0].quantity is not None:
            quantity = quotes[0].quantity or Decimal(0)
            total_line = (
                f"**{format_number(quantity, locale)} m² kaba inşaat toplamı: "
                f"{format_number(amount_total, locale, money=True)} TL + KDV**"
            )
        else:
            total_line = (
                "**Kaba inşaat toplam birim fiyatı: "
                f"{format_number(unit_total, locale, money=True)} TL/m² + KDV**\n\n"
                "Toplam metraj kaç m²?"
            )
        scope_line = (
            "Kalıp malzemesi dahildir. Demir ve duvar malzemeleri, kayıtlı fiyatları "
            "henüz bulunmadığı için bu hesaba dahil değildir."
            if material_included
            else "Demir, kalıp ve duvar malzemeleri hariçtir; yalnız işçilik hesaplanmıştır."
        )
        return "Kaba inşaat hesabı:\n" + "\n".join(lines) + f"\n\n{total_line}\n\n{scope_line}"

    def _save_rough_response(
        self,
        *,
        conversation_id: str,
        message_id: str,
        understanding_schema: str,
        text: str,
        quotes: list[QuickQuoteV1],
    ) -> ChatTurnResponseV1:
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=text,
        )
        append_outbox(
            self._session,
            event_type="customer_understanding.updated.v1",
            aggregate_id=conversation_id,
            payload={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "snapshot_schema": understanding_schema,
            },
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=text,
            quotes=quotes,
        )

    def _classify_chat_message(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ConstructionChatClassification | None:
        if self._construction_chat_provider is None:
            return None

        context = self._chat_context(
            conversation_id=conversation_id,
            locale=locale,
            quotes=[],
        )
        latest_user_message = next(
            (
                message.content
                for message in reversed(context.messages)
                if message.role.upper() == "USER"
            ),
            "",
        )
        if context.document_context and self._is_document_question(latest_user_message):
            return ConstructionChatClassification(in_scope=True, quote_intent=False)
        try:
            return self._construction_chat_provider.classify(context)
        except ConstructionChatProviderError as exc:
            self._log_chat_fallback(exc)
            return None

    def _save_scope_refusal(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ChatTurnResponseV1:
        assert self._construction_chat_provider is not None
        customer_text = self._construction_chat_provider.refusal(locale)
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="conversation.out_of_scope_refused.v1",
            aggregate_id=conversation_id,
            payload={"conversation_id": conversation_id},
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    def _answer_construction_question(
        self,
        *,
        conversation_id: str,
        locale: str,
    ) -> ChatTurnResponseV1:
        latest_user_message = next(
            (
                message.content
                for message in reversed(self._conversations.list_messages(conversation_id))
                if message.role == MessageRole.USER
            ),
            "",
        )
        fallback = self._safe_technical_fallback(latest_user_message, locale)
        customer_text = self._render_customer_response(
            conversation_id=conversation_id,
            locale=locale,
            quotes=[],
            fallback=fallback,
        )
        assistant_message = self._conversations.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=customer_text,
        )
        append_outbox(
            self._session,
            event_type="conversation.construction_chat_answered.v1",
            aggregate_id=conversation_id,
            payload={"conversation_id": conversation_id},
        )
        return ChatTurnResponseV1(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            assistant_text=customer_text,
            quotes=[],
        )

    def _render_customer_response(
        self,
        *,
        conversation_id: str,
        locale: str,
        quotes: list[QuickQuoteV1],
        fallback: str,
    ) -> str:
        if self._construction_chat_provider is None:
            return fallback

        context = self._chat_context(
            conversation_id=conversation_id,
            locale=locale,
            quotes=quotes,
        )
        safe_fallback = self._document_analysis_fallback(context) or fallback
        try:
            natural_text = self._construction_chat_provider.respond(context)
        except ConstructionChatProviderError as exc:
            self._log_chat_fallback(exc)
            with_legal_sources = self._append_legal_sources(
                safe_fallback,
                context.legal_context,
            )
            return self._append_document_sources(
                with_legal_sources,
                context.document_context,
            )

        monetary_output_is_unsafe = MONEY_PATTERN.search(natural_text) and (
            bool(quotes) or not context.legal_context
        )
        if (
            monetary_output_is_unsafe
            or INTERNAL_OUTPUT_PATTERN.search(natural_text)
            or self._technical_output_is_unsafe(natural_text, context)
        ):
            logger.warning(
                "construction_chat_unsafe_output_blocked",
                provider=self._construction_chat_provider.provider_name,
            )
            with_legal_sources = self._append_legal_sources(
                safe_fallback,
                context.legal_context,
            )
            return self._append_document_sources(
                with_legal_sources,
                context.document_context,
            )
        has_commercial_result = any(
            quote.status in {"QUOTED", "PRICE_NOT_CONFIGURED"} for quote in quotes
        )
        if not has_commercial_result:
            with_legal_sources = self._append_legal_sources(
                natural_text,
                context.legal_context,
            )
            return self._append_document_sources(
                with_legal_sources,
                context.document_context,
            )
        return f"{fallback}\n\n{natural_text}"

    @staticmethod
    def _technical_output_is_unsafe(
        text: str,
        context: ConstructionChatContext,
    ) -> bool:
        allowed_source_ids = {
            *(str(source["id"]) for source in context.legal_context),
            *(str(source["id"]) for source in context.document_context),
        }
        cited_source_ids = set(re.findall(r"\[((?:K|D)\d+)\]", text, flags=re.IGNORECASE))
        if not cited_source_ids.issubset(allowed_source_ids):
            return True

        latest_user_message = next(
            (
                message.content
                for message in reversed(context.messages)
                if message.role.upper() == "USER"
            ),
            "",
        )
        document_question = bool(
            context.document_context
        ) and QuickQuoteService._is_document_question(latest_user_message)
        if document_question and not any(
            source_id.startswith("D") for source_id in cited_source_ids
        ):
            return True

        without_list_numbers = re.sub(r"(?m)^\s*\d+[.)]\s*", "", text)
        without_citations = re.sub(
            r"\[(?:K|D)\d+\]",
            "",
            without_list_numbers,
            flags=re.IGNORECASE,
        )
        without_trusted_concrete_classes = re.sub(
            r"\bC(?:30/37|35/45)\b",
            "",
            without_citations,
            flags=re.IGNORECASE,
        )
        numeric_claims = set(re.findall(r"\b\d+(?:[.,/]\d+)*\b", without_trusted_concrete_classes))
        if numeric_claims:
            reference_text = " ".join(
                [
                    *(message.content for message in context.messages if message.role == "USER"),
                    *(str(source.get("excerpt", "")) for source in context.legal_context),
                    *(
                        f"{source.get('excerpt', '')} "
                        f"{source.get('metrics', '')} {source.get('summary', '')}"
                        for source in context.document_context
                    ),
                ]
            )
            if any(number not in reference_text for number in numeric_claims):
                return True

        normalized_parts = [
            re.sub(r"\s+", " ", part).strip().casefold()
            for part in re.split(r"[.!?\n]+", text)
            if len(part.strip()) >= 30
        ]
        return len(normalized_parts) != len(set(normalized_parts))

    @staticmethod
    def _document_analysis_fallback(context: ConstructionChatContext) -> str | None:
        latest_user_message = next(
            (
                message.content
                for message in reversed(context.messages)
                if message.role.upper() == "USER"
            ),
            "",
        )
        if not context.document_context or not QuickQuoteService._is_document_question(
            latest_user_message
        ):
            return None

        document = context.document_context[-1]
        source_id = str(document["id"])
        filename = str(document["filename"])
        summary = str(document.get("summary") or "Dosya başarıyla analiz edildi.")
        excerpt = re.sub(r"\s+", " ", str(document.get("excerpt") or "")).strip()
        lines = [
            "Yüklenen dosyanın doğrulanmış yerel analiz sonucu:",
            f"- **{filename}:** {summary} [{source_id}]",
        ]
        if excerpt:
            lines.append(f"- **Okunan metin:** “{excerpt[:1200]}” [{source_id}]")
        else:
            lines.append(f"- Dosyada güvenilir biçimde okunabilen metin bulunamadı. [{source_id}]")
        return "\n".join(lines)

    @staticmethod
    def _safe_technical_fallback(message: str, locale: str) -> str:
        if locale != "tr":
            return (
                "Check the approved project, dimensions and levels, temporary stability, "
                "connections, site safety, and inspection records before proceeding. A qualified "
                "engineer should confirm project-specific requirements on site."
            )

        folded = fold_text(message)
        if QuickQuoteService._is_project_reporting_or_scheduling_turn(message):
            return (
                "Kaba inşaat tamamlandıysa önce mevcut durumu kayıt altına alıp ince imalatları "
                "ölçülebilir bir iş programına bağlayalım:\n"
                "- Kat ve mahal bazlı eksik/kusur listesi\n"
                "- Her imalat için metraj, mevcut ilerleme ve kalan miktar\n"
                "- İşlerin öncelik ve bağımlılık sırası\n"
                "- Ekip, malzeme ve onay ihtiyaçları\n"
                "- Hedef tarihlere bağlı haftalık uygulama ve kontrol planı\n\n"
                "Başlamak için güncel mimari projeyi, kat/mahal listesini, sahadaki eksikleri "
                "ve hedef teslim tarihini paylaşın; rapor ile iş programını aynı veri üzerinden "
                "hazırlayalım."
            )
        if "kalip" in folded:
            return (
                "Kalıp uygulamasında şu sırayla kontrol edin:\n"
                "- Onaylı projedeki aks, kot, kesit ve ölçüler\n"
                "- Kalıp yüzeyi, birleşimler ve beton şerbetine karşı sızdırmazlık\n"
                "- Dikme, payanda, çapraz ve mesnetlerin stabilitesi\n"
                "- Donatı, pas payı, tesisat geçişleri ve gömülü parçalar\n"
                "- Döküm sırasında kaçak, açılma ve aşırı deformasyon takibi\n"
                "- Sökümün proje, beton dayanımı ve yetkili mühendis onayına göre yapılması\n\n"
                "Kesin uygulama; kalıp sistemi, eleman tipi ve döşeme yüksekliğine göre "
                "sahada doğrulanmalıdır."
            )
        if any(marker in folded for marker in ("demir", "donati")):
            return (
                "Donatı uygulamasında onaylı statik projedeki çap, adet, aralık, bindirme ve "
                "ankrajları esas alın. Donatının temizliği, sabitlenmesi, pas payı, sehpa ve "
                "gömülü parçaları beton öncesi kontrol tutanağıyla doğrulayın. Projede bulunmayan "
                "bir donatı ölçüsünü sahada tahmin ederek değiştirmeyin."
            )
        if "duvar" in folded:
            return (
                "Duvar uygulamasında aks, kot, düşeylik, derz düzeni, kapı-pencere boşlukları, "
                "lentolar ve taşıyıcı sisteme bağlantılar projeye göre kontrol edilmelidir. "
                "Tesisat geçişleri önceden koordine edilmeli; malzeme ve harç/adeziv üretici "
                "talimatına uygun uygulanmalıdır."
            )
        return (
            "Uygulamaya başlamadan onaylı proje ve şartnameyi, ölçü/aks/kotları, malzeme "
            "uygunluğunu, geçici stabiliteyi, iş güvenliğini ve saha kontrol kayıtlarını birlikte "
            "doğrulayın. Yapıya özgü kararlar yetkili mühendis ve yerinde incelemeyle "
            "netleştirilmelidir."
        )

    def _chat_context(
        self,
        *,
        conversation_id: str,
        locale: str,
        quotes: list[QuickQuoteV1],
    ) -> ConstructionChatContext:
        history = self._conversations.list_messages(conversation_id)
        latest_user_message = next(
            (message.content for message in reversed(history) if message.role == MessageRole.USER),
            "",
        )
        legal_hits = (
            self._legal_knowledge.search(latest_user_message, limit=3)
            if self._legal_knowledge is not None
            and latest_user_message
            and not quotes
            and self._needs_legal_sources(latest_user_message)
            else []
        )
        return ConstructionChatContext(
            locale=locale,
            messages=[
                ChatMessage(role=message.role.value, content=message.content) for message in history
            ],
            quote_results=[self._quote_context(quote) for quote in quotes],
            legal_context=[
                {
                    "id": f"K{index}",
                    "title": hit.title,
                    "category": hit.category,
                    "authority": hit.authority,
                    "source_url": hit.source_url,
                    "fetched_at": hit.fetched_at,
                    "excerpt": hit.content[:1100],
                }
                for index, hit in enumerate(legal_hits, start=1)
            ],
            document_context=[
                {
                    "id": f"D{index}",
                    "analysis_id": analysis.analysis_id,
                    "filename": analysis.filename,
                    "format": analysis.format,
                    "summary": analysis.summary,
                    "excerpt": analysis.extracted_text[:3500],
                    "metrics": self._document_metrics_context(analysis.metrics),
                    "sha256": analysis.sha256,
                }
                for index, analysis in (
                    list(
                        enumerate(
                            self._document_analyses.list_for_conversation(conversation_id),
                            start=1,
                        )
                    )[-3:]
                    if self._document_analyses is not None
                    else []
                )
            ],
        )

    @staticmethod
    def _document_metrics_context(metrics: dict[str, Any]) -> dict[str, Any]:
        bounded: dict[str, Any] = {}
        for key, value in metrics.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                bounded[key] = value
            elif key in {"ocr_pages", "layers"} and isinstance(value, list):
                bounded[key] = value[:50]
            elif key in {"entity_types", "element_types"} and isinstance(value, dict):
                bounded[key] = dict(list(value.items())[:50])
        return bounded

    @staticmethod
    def _needs_legal_sources(message: str) -> bool:
        folded = fold_text(message)
        return any(
            re.search(rf"(?<!\w){re.escape(marker)}\w*", folded) is not None
            for marker in LEGAL_QUERY_MARKERS
        )

    @staticmethod
    def _is_document_question(message: str) -> bool:
        folded = fold_text(message)
        return any(marker in folded for marker in DOCUMENT_QUERY_MARKERS)

    @staticmethod
    def _append_legal_sources(text: str, legal_context: list[dict[str, Any]]) -> str:
        if not legal_context:
            return text
        seen_urls: set[str] = set()
        source_lines: list[str] = []
        for source in legal_context:
            url = str(source["source_url"])
            if url in seen_urls:
                continue
            seen_urls.add(url)
            fetched_on = str(source["fetched_at"])[:10]
            source_lines.append(
                f"- [{source['id']}] {source['title']} — {url} (erişim: {fetched_on})"
            )
        return f"{text}\n\nResmî kaynaklar:\n" + "\n".join(source_lines)

    @staticmethod
    def _append_document_sources(
        text: str,
        document_context: list[dict[str, Any]],
    ) -> str:
        cited_ids = set(re.findall(r"\[(D\d+)\]", text, flags=re.IGNORECASE))
        source_lines = [
            f"- [{source['id']}] {source['filename']} ({source['format'].lstrip('.').upper()})"
            for source in document_context
            if str(source["id"]) in cited_ids
        ]
        if not source_lines:
            return text
        return f"{text}\n\nProje kaynakları:\n" + "\n".join(source_lines)

    def _log_chat_fallback(self, exc: ConstructionChatProviderError) -> None:
        if self._construction_chat_provider is None:
            return
        logger.warning(
            "construction_chat_fallback",
            provider=self._construction_chat_provider.provider_name,
            error_type=type(exc).__name__,
        )

    @staticmethod
    def _quote_context(quote: QuickQuoteV1) -> dict[str, Any]:
        return {
            "status": quote.status,
            "service": quote.service_label,
            "verified_commercial_result_available": quote.unit_price is not None,
            "scope_summary": quote.scope_summary,
            "clarification_question": quote.clarification_question,
        }

    def _process_understood_job(
        self,
        *,
        conversation_id: str,
        locale: str,
        understood: JobUnderstandingV1,
        existing_jobs: list[CustomerJob],
        is_multi_job: bool,
    ) -> QuickQuoteV1:
        target = self._target_job(understood, existing_jobs, is_multi_job)
        service_code = understood.service_candidate or (target.service_code if target else None)
        quantity = (
            understood.quantity.value
            if understood.quantity
            else (target.quantity if target else None)
        )
        unit = (
            understood.quantity.unit if understood.quantity else (target.unit if target else None)
        )

        explicit_location = None
        if understood.location:
            explicit_location = understood.location.code or understood.location.display_name
        inherited_location = target.location_code if target else None
        location = self._location_normalizer.normalize(
            explicit_location or inherited_location,
            self._default_location_code,
        )

        service = self._services.get_active(service_code) if service_code else None
        package = self._work_packages.get_active(service_code) if service_code else None
        scope_state = self._build_scope_state(
            package=package,
            exclusions=understood.customer_exclusions,
            additions=understood.customer_additions,
            locale=locale,
        )
        job = self._conversations.save_job(
            conversation_id=conversation_id,
            job_id=target.id if target else None,
            service_code=service_code,
            quantity=quantity,
            unit=unit,
            location_code=location.code,
            location_label=location.display_name,
            scope_state=scope_state,
            provenance=[item.model_dump(mode="json") for item in understood.evidence],
        )
        append_outbox(
            self._session,
            event_type="job.updated.v1" if target else "job.created.v1",
            aggregate_id=job.id,
            payload={"conversation_id": conversation_id, "job_id": job.id},
        )

        if service is None or service_code is None:
            needs_facade_system = FACADE_SERVICE_CONSTRAINT in understood.constraints
            clarification_question = None
            if needs_facade_system:
                clarification_question = (
                    "Kompozit, mantolama ve boya veya farklı bir cephe sistemi mi uygulanacak?"
                    if locale == "tr"
                    else "Will this be composite cladding, insulation and paint, or "
                    "another facade system?"
                )
            return QuickQuoteV1(
                job_id=job.id,
                service_code=service_code or "unclassified",
                service_label=(
                    "Dış Cephe"
                    if locale == "tr" and needs_facade_system
                    else "Facade"
                    if needs_facade_system
                    else "Yapım işi"
                    if locale == "tr"
                    else "Construction work"
                ),
                location_code=location.code,
                location_label=location.display_name,
                quantity=quantity,
                unit=unit or "m2",
                status="SERVICE_CLARIFICATION_REQUIRED",
                scope_summary="",
                scope_items=scope_state,
                clarification_question=clarification_question,
                customer_text="",
            )

        price = self._price_resolver.resolve(
            service_code=service_code,
            location_code=location.code,
        )
        scope_summary = package.scope_summary(locale) if package else ""
        if price is None:
            append_outbox(
                self._session,
                event_type="price.missing.v1",
                aggregate_id=job.id,
                payload={
                    "service_code": service_code,
                    "location_code": location.code,
                },
            )
            append_audit(
                self._session,
                action="PRICE_NOT_CONFIGURED",
                aggregate_id=job.id,
                detail={
                    "service_code": service_code,
                    "location_code": location.code,
                },
            )
            return QuickQuoteV1(
                job_id=job.id,
                service_code=service_code,
                service_label=service.label(locale),
                location_code=location.code,
                location_label=location.display_name,
                quantity=quantity,
                unit=unit or "m2",
                status="PRICE_NOT_CONFIGURED",
                scope_summary=scope_summary,
                scope_items=scope_state,
                customer_text="",
            )

        normalized_quantity = quantity if (unit or price.unit) == price.unit else None
        total = calculate_total(normalized_quantity, price.price) if normalized_quantity else None
        quote_id = self._quotes.save(
            conversation_id=conversation_id,
            job_id=job.id,
            service_code=service_code,
            price_book_id=price.id,
            price_version=price.version,
            quantity=normalized_quantity,
            unit_price=price.price,
            total_price=total,
            currency=price.currency,
            vat_policy=price.vat_policy,
        )
        append_outbox(
            self._session,
            event_type="quick_quote.generated.v1",
            aggregate_id=quote_id,
            payload={
                "conversation_id": conversation_id,
                "job_id": job.id,
                "price_version": price.version,
            },
        )
        return QuickQuoteV1(
            quote_id=quote_id,
            job_id=job.id,
            service_code=service_code,
            service_label=service.label(locale),
            location_code=location.code,
            location_label=location.display_name,
            quantity=normalized_quantity,
            unit=price.unit,
            unit_price=price.price,
            total_price=total,
            currency=price.currency,
            vat_policy=price.vat_policy,
            price_version=price.version,
            status="QUOTED",
            scope_summary=scope_summary,
            scope_items=scope_state,
            customer_text="",
        )

    @staticmethod
    def _target_job(
        understood: JobUnderstandingV1,
        existing_jobs: list[CustomerJob],
        is_multi_job: bool,
    ) -> CustomerJob | None:
        if understood.job_reference:
            return next(
                (job for job in existing_jobs if job.id == understood.job_reference),
                None,
            )
        if is_multi_job or not existing_jobs:
            return None
        latest = existing_jobs[-1]
        if understood.service_candidate is None:
            return None
        if latest.service_code is None:
            return latest
        service_family = (
            {"formwork.labor", "formwork.labor_material"}
            if understood.service_candidate in {"formwork.labor", "formwork.labor_material"}
            else {understood.service_candidate}
        )
        pending_match = next(
            (
                job
                for job in reversed(existing_jobs)
                if job.service_code in service_family and job.quantity is None
            ),
            None,
        )
        if pending_match is not None:
            return pending_match
        if understood.location is None or understood.action in {
            ConversationActionV1.UPDATE,
            ConversationActionV1.REMOVE,
            ConversationActionV1.QUERY,
        }:
            existing_match = next(
                (job for job in reversed(existing_jobs) if job.service_code in service_family),
                None,
            )
            if existing_match is not None:
                return existing_match
        return None

    @staticmethod
    def _build_scope_state(
        *,
        package: WorkPackage | None,
        exclusions: list[str],
        additions: list[str],
        locale: str,
    ) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        package_codes: set[str] = set()
        if package:
            for item in package.items:
                package_codes.add(item.item_code)
                state = "INCLUDED_BY_DEFAULT" if item.default_included else "OPTIONAL"
                if item.item_code in exclusions:
                    state = "EXCLUDED_BY_CUSTOMER"
                elif item.item_code in additions:
                    state = "INCLUDED_BY_CUSTOMER"
                states.append(
                    {
                        "item_code": item.item_code,
                        "label": item.label(locale),
                        "state": state,
                        "supply_by": item.supply_by.value,
                        "pricing_role": item.pricing_role.value,
                    }
                )

        for code in additions:
            if code not in package_codes:
                states.append(
                    {
                        "item_code": code,
                        "label": code,
                        "state": "INCLUDED_BY_CUSTOMER",
                        "supply_by": "EITHER",
                        "pricing_role": "ADD_ON",
                    }
                )
        return states
