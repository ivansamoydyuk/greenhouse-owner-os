from uuid import UUID


class HistoryServiceMixin:
    def get_cutover_status(self, entity_id):
        with self.uow_factory() as uow:
            cutoff, opening = uow.finance.get_cutover(entity_id)
            result = {'entity_id':str(entity_id), 'cutover_date':cutoff.isoformat(),
                    'opening_policy':opening, 'opening_balances':dict.fromkeys(
                        ['cash', 'payable', 'receivable', 'supplier_advances', 'customer_advances'], '0.00'),
                    'opening_postings_generated':False, 'historical_posting_policy':'NON_POSTING',
                    'live_posting_from':cutoff.isoformat()}
            openings = uow.finance.get_opening_payables(entity_id)
            if openings:
                from decimal import Decimal
                by_currency = {}
                for row in openings:
                    by_currency[row['currency']] = by_currency.get(row['currency'], Decimal('0.00')) + Decimal(row['amount'])
                result['opening_balances']['payable'] = {currency:f'{amount:.2f}' for currency,amount in sorted(by_currency.items())}
                result['opening_postings_generated'] = True
                result['opening_payables'] = openings
                result['opening_balance_policy'] = 'ZERO_OTHER_POSITIONS_WITH_EXPLICIT_VERIFIED_PAYABLES'
            return result

    def create_import_batch(self, *, actor_id, idempotency_key, command):
        return self._import_evidence('import_batch', actor_id, idempotency_key, command)

    def import_historical_candidate(self, *, actor_id, idempotency_key, batch_id, command):
        return self._import_evidence('historical_candidate', actor_id, idempotency_key, command, batch_id)

    def _import_evidence(self, kind, actor_id, key, command, batch_id=None):
        from .finance import _hash_request
        operation = 'import_' + kind
        payload = command.model_dump(mode='json')
        if kind == 'historical_candidate' and command.confidence is not None:
            payload['confidence'] = str(command.confidence.normalize())
        if batch_id is not None: payload['import_batch_id'] = str(batch_id)
        request_hash = _hash_request(payload)
        with self.uow_factory() as uow:
            uow.idempotency.lock(actor_id, operation, key)
            replay = self._replay_if_exists(uow, actor_id, operation, key, request_hash)
            if replay: return replay
            values = command.model_dump(mode='json' if kind == 'historical_candidate' else 'python')
            if kind == 'import_batch':
                uow.finance.get_cutover(command.entity_id)
                response, created = uow.finance.ensure_import_batch({**values, 'created_by':actor_id})
            else:
                values = command.model_dump()
                # Locator identity uses both original components; never amount/date
                # or import timestamp. Same source locator with changed content conflicts.
                source_key = _hash_request({'source_row':command.source_row, 'source_document':command.source_document})
                evidence = command.model_dump(mode='json')
                # Decimal representation must not create false retry/content collisions.
                if command.confidence is not None: evidence['confidence'] = str(command.confidence.normalize())
                response, created = uow.finance.ensure_historical_candidate({**values, 'import_batch_id':batch_id,
                    'source_key':source_key, 'content_hash':_hash_request(evidence), 'created_by':actor_id})
            if created:
                uow.audit.append(actor_id=actor_id, action='import', entity_type=kind,
                    entity_id=UUID(response['id']), before=None, after=response)
            uow.idempotency.put(actor_id=actor_id, operation=operation, key=key, request_hash=request_hash,
                resource_type=kind, resource_id=UUID(response['id']), response=response)
            uow.commit()
            return response

    def get_import_batch(self, batch_id):
        with self.uow_factory() as uow:
            return {**uow.finance.get_import_batch(batch_id), 'posting_policy':'NON_POSTING',
                    'candidates':uow.finance.historical_candidates(batch_id)}

    def get_historical_candidate(self, candidate_id):
        with self.uow_factory() as uow:
            return uow.finance.get_historical_candidate(candidate_id)
