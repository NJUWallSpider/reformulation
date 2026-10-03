"""Small static diagnostic links; never load the search corpus on an error path."""
CLASS_TOPICS = {
 'ExactArithmeticError':'concept.exact', 'NonlinearExpressionError':'error.expressions',
 'MissingBoundError':'concept.proofs', 'InvalidCertificateError':'concept.proofs',
 'UnsupportedEnumerationDomain':'api.Checker.enumerate', 'CardinalityError':'error.join',
 'ValidationError':'concept.proofs', 'SearchBudgetExceeded':'concept.search',
 'QueryTooLarge':'concept.resources','DiskBudgetExceeded':'concept.resources',
 'QueryMemoryError':'concept.resources','MaterializationLimitError':'concept.resources',
 'GraphCycleError':'api.ops.graph.topological_order','SchemaError':'concept.frame',
 'EditConflictError':'api.Edit','EditValidationError':'api.Edit.build',
 'UndischargedAssumptionError':'concept.conditions','NonIsolatedVariableError':'limitation.elimination',
 'MIPFrameError':'concept.errors',
}


def hint(exc, topic):
    """Preserve exception identity, args, and attached evidence; add a help link."""
    exc.help_topic = topic
    notes = [n for n in getattr(exc, '__notes__', ()) if not n.startswith('Help: mf.help(')]
    exc.__notes__ = notes
    exc.add_note(f'Help: mf.help({topic!r})')
    return exc


def annotate(exc):
    for cls in type(exc).__mro__:
        topic = CLASS_TOPICS.get(cls.__name__)
        if topic:
            hint(exc,topic)
            return
