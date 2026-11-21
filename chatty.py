import os
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
import argparse
import json
import operator
import functools
from copy import deepcopy
from time import sleep, perf_counter
from base64 import b64encode
from pathlib import Path
from datetime import date
from jinja2 import Environment, PackageLoader, select_autoescape

US_CORE_RACE = 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-race'

# ---------- ADDED: validation configuration (maps to patent §5.2 / §5.3) ----------
VALID_GENDERS = {'male', 'female', 'other', 'unknown'}
VALID_OBS_STATUS = {
    'registered', 'preliminary', 'final', 'amended',
    'corrected', 'cancelled', 'entered-in-error', 'unknown',
}
HEART_RATE_LOINC = '8867-4'
HR_MIN, HR_MAX = 40, 180          # screening interval from the patent's validator
HR_FAIL_TOLERANCE = 0.05          # patent: success if >=95% of values in range
# ---------------------------------------------------------------------------------


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Tool for transforming clinical notes in a FHIR Bundle with ChatGPT"
    )
    parser.add_argument(
        "-b",
        "--bundle",
        dest="bundle",
        required=True,
        help="The filename of the input FHIR Bundle",
    )
    # ---------- ADDED: optional --validate flag (the patent's gate) ----------
    parser.add_argument(
        "-v",
        "--validate",
        dest="validate",
        action="store_true",
        help="Run pre-generation data-quality validation on the FHIR bundle "
             "before sending any prompt to the LLM.",
    )
    # -----------------------------------------------------------------------
    args = parser.parse_args()
    if not Path(args.bundle).exists():
        parser.error("Unable to find bundle file: " + args.bundle)
    return args


def parse_bundle_json(bundle_filepath):
    with open(bundle_filepath, "r") as json_fp:
        bundle = json.load(json_fp)
    return bundle


# ---------- ADDED: the ingestion-time validation gate (patent §5.6 controller) ----------
def validate_bundle(bundle):
    """Return a list of human-readable data-quality issues found in the bundle.
    Empty list => bundle passes the configured checks.
    This is the 'data-routing controller' described as a future extension in the
    provisional patent; here it is wired to gate LLM use at ingestion."""
    issues = []

    # 1) Patient resource integrity: nonblank, unique IDs + valid gender (§5.2)
    patient_ids = []
    for entry in bundle.get('entry', []):
        res = entry.get('resource', {})
        if res.get('resourceType') == 'Patient':
            pid = res.get('id')
            if not pid or not str(pid).strip():
                issues.append("Patient resource has a blank/missing id.")
            else:
                patient_ids.append(pid)
            gender = res.get('gender')
            if gender and gender not in VALID_GENDERS:
                issues.append(f"Patient {pid} has invalid gender '{gender}'.")
    if len(patient_ids) != len(set(patient_ids)):
        issues.append("Duplicate Patient ids found in bundle.")

    # 2) Observation status + heart-rate clinical plausibility screen (§5.3)
    hr_values = []
    for entry in bundle.get('entry', []):
        res = entry.get('resource', {})
        if res.get('resourceType') != 'Observation':
            continue
        status = res.get('status')
        if status and status not in VALID_OBS_STATUS:
            issues.append(f"Observation {res.get('id')} has invalid status '{status}'.")
        for coding in res.get('code', {}).get('coding', []):
            if coding.get('code') == HEART_RATE_LOINC or 'heart rate' in coding.get('display', '').lower():
                v = res.get('valueQuantity', {}).get('value')
                if v is not None:
                    try:
                        hr_values.append(float(v))
                    except (TypeError, ValueError):
                        issues.append(f"Heart-rate Observation {res.get('id')} has non-numeric value '{v}'.")

    if hr_values:
        out_of_range = [v for v in hr_values if not (HR_MIN <= v <= HR_MAX)]
        fail_ratio = len(out_of_range) / len(hr_values)
        if fail_ratio > HR_FAIL_TOLERANCE:
            sample = ', '.join(str(v) for v in out_of_range[:5])
            issues.append(
                f"Heart-rate values outside {HR_MIN}-{HR_MAX} bpm "
                f"({len(out_of_range)}/{len(hr_values)}): [{sample}]."
            )
    return issues
# ---------------------------------------------------------------------------------------


def extract_resources_by_type(bundle, resource_type):
    selected_entries = filter(lambda be: be['resource']['resourceType'] == resource_type, bundle['entry'])
    return list(map(lambda se: se['resource'], selected_entries))


def filter_resources_for_encounter(resources, encounter):
    encounter_id = encounter['id']
    encounter_reference = 'urn:uuid:' + encounter_id
    return filter(lambda resource: resource['encounter']['reference'] == encounter_reference, resources)


def extract_patient(bundle):
    patient_entry = next(filter(lambda be: be['resource']['resourceType'] == 'Patient', bundle['entry']))
    return patient_entry['resource']


def extract_document_reference(bundle, reference_id):
    dr_entry = next(filter(lambda be: be['resource']['id'] == reference_id, bundle['entry']))
    return dr_entry['resource']


def find_encounter(bundle, document_reference):
    encounter_id = document_reference['context']['encounter'][0]['reference']
    encounter_entry = next(filter(lambda be: be['fullUrl'] == encounter_id, bundle['entry']))
    return encounter_entry['resource']


def clean_condition_display(cd):
    return cd.removesuffix('(situation)')\
      .removesuffix('(finding)')\
      .removesuffix('(disorder)')\
      .strip()


def clean_encounter_type_display(etd):
    return etd.removesuffix('(procedure)')\
      .removesuffix('(environment)')\
      .lower()\
      .strip()


def procedure_display(procedure_list):
    procedures = []
    for proc in procedure_list:
        raw_name = proc['code']['coding'][0]['display']
        procedures.append(raw_name.removesuffix('(procedure)').lower().strip())
    return procedures


def extract_race(patient):
    race_extension = next(filter(lambda pe: pe['url'] == US_CORE_RACE, patient['extension']))
    return race_extension['extension'][0]['valueCoding']['display'].lower()


def extract_medication_names(meds, bundle):
    med_names = []
    for med in meds:
        med_cc = med.get('medicationCodeableConcept')
        if med_cc is not None:
            med_names.append(med_cc['coding'][0]['display'])
        else:
            referenced_med_id = med['medicationReference']['reference']
            med_entry = next(filter(lambda be: be['fullUrl'] == referenced_med_id, bundle['entry']))
            med_names.append(med_entry['resource']['code']['coding'][0]['display'])
    return med_names


def build_template_context(patient, encounter, bundle):
    context = {}
    given_name = ' '.join(patient['name'][0]['given'])
    family_name = patient['name'][0]['family']
    context['name'] = given_name + ' ' + family_name
    birth_date = date.fromisoformat(patient['birthDate'])
    encounter_date = date.fromisoformat(encounter['period']['start'][0:10])
    age = (encounter_date - birth_date).days // 365
    context['age'] = age
    context['gender'] = patient['gender']
    encounter_type = encounter['type'][0]['coding'][0]['display']
    context['encounter_type'] = clean_encounter_type_display(encounter_type)
    if 'reasonCode' in encounter:
      reason = encounter['reasonCode'][0]['coding'][0]['display']
      context['reason'] = clean_condition_display(reason)
    context['race'] = extract_race(patient)
    medications = extract_resources_by_type(bundle, 'MedicationRequest')
    encounter_medications = filter_resources_for_encounter(medications, encounter)
    context['medications'] = extract_medication_names(encounter_medications, bundle)
    procedures = extract_resources_by_type(bundle, 'Procedure')
    immunizations = extract_resources_by_type(bundle, 'Immunization')
    encounter_procedures = filter_resources_for_encounter(procedures, encounter)
    context['procedures'] = procedure_display(encounter_procedures)
    encounter_immunizations = filter_resources_for_encounter(immunizations, encounter)
    immunizations = list(map(lambda iz: iz['vaccineCode']['coding'][0]['display'], encounter_immunizations))

    return context


def create_template_environment():
    return Environment(loader=PackageLoader("chatty"),
                       autoescape=select_autoescape(), trim_blocks=True)


def generate_note(client, prompt, role, perf_log):
    # Try to call ChatGPT 4 times for this note
    start = perf_counter()
    for attempt in range(1, 5):
        try:
            response = client.chat.completions.create(
                model = "gpt-3.5-turbo",
                messages = [
                    {"role": "system", "content": role},
                    {"role": "user", "content": prompt}
                ]
            )
            ai_generated_note = response.choices[0].message.content
            complete = perf_counter()
            elapsed_time = complete - start
            perf_log.append({"time": elapsed_time, "attempts": attempt, "success": True})
            return ai_generated_note
        except AuthenticationError as exc:
            raise RuntimeError(
                "OpenAI authentication failed. Check OPENAI_API_KEY."
            ) from exc
        except (RateLimitError, APIConnectionError, APITimeoutError, APIError):
            # Sleep longer with each unsuccessful attempt to call the API
            sleep(5 * attempt)
    complete = perf_counter()
    elapsed_time = complete - start
    perf_log.append({"time": elapsed_time, "attempts": attempt, "success": False})
    raise RuntimeError('Unable to generate note after 4 tries.')


def write_output(input_file_name, output_bundle):
    if not os.path.isdir('output'):
        os.mkdir('output')
    basename = os.path.basename(input_file_name)
    output_path = Path('output') / basename
    with open(output_path, "w") as outfile:
        json.dump(output_bundle, outfile)


def main():
    args = parse_arguments()
    bundle = parse_bundle_json(args.bundle)

    # ---------- ADDED: gate the whole run on ingestion-time validation ----------
    if args.validate:
        issues = validate_bundle(bundle)
        print("\n" + "=" * 70)
        print("🛡️  PRE-GENERATION VALIDATION REPORT (chatty-notes gate)")
        print("=" * 70)
        if issues:
            for msg in issues:
                print(f"  ✗ {msg}")
            print("-" * 70)
            print("RESULT: bundle REJECTED — prompt NOT sent to LLM (quarantined).")
            print("=" * 70 + "\n")
            return  # <-- the controller: stop before any downstream use
        else:
            print("  ✓ Patient id/gender checks passed")
            print("  ✓ Observation status checks passed")
            print("  ✓ Heart-rate values within 40–180 bpm screen")
            print("-" * 70)
            print("RESULT: bundle PASSED — proceeding to note generation.")
            print("=" * 70 + "\n")
    # ---------------------------------------------------------------------------

    # Create a copy of the bundle to write to file so we don't have to mutate
    # the original.
    output_bundle = deepcopy(bundle)
    patient = extract_patient(bundle)
    template_env = create_template_environment()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export an API key before running chatty."
        )
    client = OpenAI(api_key=api_key)
    encounter_for_problem_template = template_env.get_template('encounter_for_problem.txt.jinja')
    er_template = template_env.get_template('emergency_room.txt.jinja')
    death_cert_template = template_env.get_template('death_certification.txt.jinja')
    oa_template = template_env.get_template('oa_encounter.txt.jinja')
    perf_log = []
    for dr in extract_resources_by_type(bundle, 'DocumentReference'):
        encounter = find_encounter(bundle, dr)
        context = build_template_context(patient, encounter, bundle)
        prompt = None
        system_role = "You are a medical scribe."
        if encounter['type'][0]['coding'][0]['code'] == '50849002':
          prompt = er_template.render(context)
        elif encounter['type'][0]['coding'][0]['code'] == '308646001':
          prompt = death_cert_template.render(context)
          system_role = "You are a medical examiner."
        elif 'reasonCode' in encounter:
            if encounter['reasonCode'][0]['coding'][0]['code'] == '239873007':
                prompt = oa_template.render(context)
            else:
                prompt = encounter_for_problem_template.render(context)

        if prompt is not None:
          ai_generated_note = generate_note(client, prompt, system_role, perf_log)
          encoded_note = b64encode(ai_generated_note.encode('utf-8')).decode()
          reference_id = dr['id']
          output_dr = extract_document_reference(output_bundle, reference_id)
          output_dr['content'][0]['attachment']['data'] = encoded_note
    write_output(args.bundle, output_bundle)
    total_note_count = len(perf_log)
    successful = len(list(filter(lambda row: row["success"] == True, perf_log)))
    failed = len(list(filter(lambda row: row["success"] == False, perf_log)))
    total_time = functools.reduce(operator.add, map(lambda row: row["time"], perf_log))
    average_time = total_time / total_note_count
    print(f'Total notes: {total_note_count}')
    print(f'Success: {successful}')
    print(f'Failed: {failed}')
    print(f'Average time: {average_time}')


if __name__ == "__main__":
    main()