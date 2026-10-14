# Functions to aid describing variants and their sigs
# Brianna Argall
# 2026/02/17

# E.g. repr_cannonical_iv(nsclc_sqcc.studies[0].variants[0].component_sigs)
def repr_cannonical_iv(comp_sigs):
    lines = []
    lines.append(variant_header(**comp_sigs[0].__dict__))
    for component_obj in comp_sigs:
        lines.append(sig_component_obj(**component_obj.__dict__))
    return f"\n".join(lines)


def sig_component_obj(*, doseminnum, dosemaxnum, doseunit,
                               dosecapnum, dosecapunit,
                              durationunit, durationmaxnum, durationminnum,
                              route, frequency, alldays, timing_sequence, component,
                               seqrel_items, seqrelwhen_items, seqrelwhat=None,
                               sequence, id, phase, seqrelwhenunit_items, divided,
                              **kwargs):
    dose_size = sig_comp_dose_size(doseminnum, dosemaxnum)
    dose_cap = sig_dose_cap(dosecapnum, dosecapunit)
    duration = sig_duration(durationunit, durationminnum, durationmaxnum)
    # TODO: Handle parts
    sequencing = sig_sequencing(sequence, seqrel_items, seqrelwhen_items, seqrelwhenunit_items, seqrelwhat)
    if frequency:
        per_cycle = f"{dose_size} {doseunit}{dose_cap} {route}{duration} {frequency.value} on day(s) {alldays}"
    elif divided:
        per_cycle = f"{dose_size} {doseunit}{dose_cap} {route}{duration} in divided doses on day(s) {alldays}"
    else:
        per_cycle = f"{dose_size} {doseunit}{dose_cap} {route}{duration} on day(s) {alldays}"
    return f"{component}: {per_cycle} in cycle(s) {timing_sequence}. {phase} {sequencing}SigID: {id}"


def variant_header(regimen, variant, variant_cui, regimen_cui, cyclesigs, **kwargs):
    return f"{regimen}: {regimen_cui}, {variant}: {variant_cui}. {cyclesigs}"


def sig_comp_dose_size(doseminnum, dosemaxnum):
    if doseminnum == dosemaxnum:
        dose_size = doseminnum
    else:
        dose_size = f"{doseminnum}-{dosemaxnum}"
    return str(dose_size)


def sig_duration(durationunit, durationminnum, durationmaxnum):
    if durationminnum and durationmaxnum:
        if durationminnum == durationmaxnum:
            duration = " over " + str(durationminnum) + " " + durationunit + "s"
        else:
            duration = " over " + str(durationminnum) + " to " + str(durationmaxnum) + " " + durationunit + "s"
    else:
        duration = ""
    return duration


def sig_dose_cap(dosecapnum, dosecapunit):
    if dosecapnum and dosecapunit:
        dosecap = " (" + str(dosecapnum) + " " + str(dosecapunit.value) + ")"
    else: dosecap = ""
    return dosecap


def sig_sequencing(sequence, seqrel_items, seqrelwhen_items, seqrelwhenunit_items, seqrelwhat):
    # deal with seqrelwhen when decent example
    sequencing = ""
    if sequence:
        sequencing += f"Timing relative to other components: {sequence}. "
    if seqrelwhen_items:
        sequencing += f"{seqrelwhen_items[0].seqrelwhen} "
    if seqrelwhenunit_items:
        sequencing += f"{seqrelwhenunit_items[0].seqrelwhenunit}(s) "
    if seqrel_items:
        sequencing += f"{seqrel_items[0].seqrel} {seqrelwhat}. "
    return sequencing


