from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.contracts.v1.customer_understanding import (
    FACADE_SERVICE_CONSTRAINT,
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
from app.modules.customer_understanding.application.service import CustomerUnderstandingService
from app.modules.customer_understanding.infrastructure.location import fold_text
from app.modules.legal_knowledge.domain.ports import LegalKnowledgeSearch
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
    r"clarification_question|system prompt|JSON (?:şeması|schema)",
    re.IGNORECASE,
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
        existing_jobs = self._conversations.list_jobs(conversation_id)
        if self._is_rough_construction_turn(text, existing_jobs):
            return self._handle_rough_construction(
                conversation_id=conversation_id,
                locale=conversation.locale,
                message_id=message.id,
                text=text,
                existing_jobs=existing_jobs,
            )
        if self._is_fine_construction_turn(text):
            return self._handle_fine_construction(
                conversation_id=conversation_id,
                locale=conversation.locale,
            )
        if self._is_total_summary_request(text):
            return self._handle_total_summary(
                conversation_id=conversation_id,
                locale=conversation.locale,
            )

        chat_classification = self._classify_chat_message(
            conversation_id=conversation_id,
            locale=conversation.locale,
        )
        if chat_classification is not None:
            if not chat_classification.in_scope:
                return self._save_scope_refusal(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
                )
            if not chat_classification.quote_intent:
                return self._answer_construction_question(
                    conversation_id=conversation_id,
                    locale=conversation.locale,
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
            "genel toplam" in folded
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
        jobs = {job.id: job for job in self._conversations.list_jobs(conversation_id)}
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
        understood = understanding.jobs[0]
        pending = next(
            (
                job
                for job in reversed(existing_jobs)
                if job.service_code == "rough_construction.turnkey"
                and (not job.scope_state or job.quantity is None)
            ),
            None,
        )
        quantity = understood.quantity.value if understood.quantity else None
        unit = understood.quantity.unit if understood.quantity else None
        if quantity is None and pending is not None:
            quantity = pending.quantity
            unit = pending.unit

        explicit_location = None
        if understood.location:
            explicit_location = understood.location.code or understood.location.display_name
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
                provenance=[item.model_dump(mode="json") for item in understood.evidence],
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
            provenance=[item.model_dump(mode="json") for item in understood.evidence],
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
        if any(
            marker in folded
            for marker in ("malzemesiz", "malzeme haric", "sadece iscilik")
        ):
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
        fallback = (
            "Bu teknik soruyu şu anda ayrıntılı yanıtlayamıyorum. "
            "İsterseniz yapınızın türünü ve uygulama koşullarını paylaşın."
            if locale == "tr"
            else "I cannot answer that technical question in detail right now. "
            "You can share the building type and application conditions."
        )
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
        try:
            natural_text = self._construction_chat_provider.respond(context)
        except ConstructionChatProviderError as exc:
            self._log_chat_fallback(exc)
            return self._append_legal_sources(fallback, context.legal_context)

        monetary_output_is_unsafe = MONEY_PATTERN.search(natural_text) and (
            bool(quotes) or not context.legal_context
        )
        if monetary_output_is_unsafe or INTERNAL_OUTPUT_PATTERN.search(natural_text):
            logger.warning(
                "construction_chat_unsafe_output_blocked",
                provider=self._construction_chat_provider.provider_name,
            )
            return self._append_legal_sources(fallback, context.legal_context)
        has_commercial_result = any(
            quote.status in {"QUOTED", "PRICE_NOT_CONFIGURED"} for quote in quotes
        )
        if not has_commercial_result:
            return self._append_legal_sources(natural_text, context.legal_context)
        return f"{fallback}\n\n{natural_text}"

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
            if self._legal_knowledge is not None and latest_user_message and not quotes
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
        )

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
        if understood.location is None:
            existing_match = next(
                (
                    job
                    for job in reversed(existing_jobs)
                    if job.service_code in service_family
                ),
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
