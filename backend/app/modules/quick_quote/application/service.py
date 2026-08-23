from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.contracts.v1.customer_understanding import (
    FACADE_SERVICE_CONSTRAINT,
    CustomerUnderstandingContextV1,
    ExistingJobContextV1,
    JobUnderstandingV1,
)
from app.contracts.v1.quick_quote import ChatTurnResponseV1, QuickQuoteV1
from app.core.persistence import append_audit, append_outbox
from app.modules.conversation.domain.models import CustomerJob, MessageRole
from app.modules.conversation.infrastructure.repository import ConversationRepository
from app.modules.customer_understanding.application.service import CustomerUnderstandingService
from app.modules.pricing.application.service import PriceResolver
from app.modules.pricing.domain.location import LocationNormalizer
from app.modules.quick_quote.application.presentation import render_quote_response
from app.modules.quick_quote.domain.models import calculate_total
from app.modules.quick_quote.infrastructure.repository import QuickQuoteRepository
from app.modules.service_catalog.infrastructure.repository import ServiceCatalogRepository
from app.modules.work_package.domain.models import WorkPackage
from app.modules.work_package.infrastructure.repository import WorkPackageRepository


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
        customer_text = render_quote_response(
            quick_quotes,
            understanding.detected_language or conversation.locale,
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
                    "Kompozit, mantolama ve boya veya farklı bir cephe sistemi mi "
                    "uygulanacak?"
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
            return latest
        if latest.service_code is None:
            return latest
        if understood.service_candidate == latest.service_code and understood.location is None:
            return latest
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
