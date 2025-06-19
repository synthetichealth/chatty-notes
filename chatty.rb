require 'json'
require 'base64'
require 'pry'
require_relative 'ruby/bundle'
require_relative 'ruby/llm'

MAX_HISTORY = 3

CLASS_DISPLAY = {
  'AMB' => 'outpatient',
  'EMER' => 'emergency',
  'HH' => 'home health',
  'IMP' => 'inpatient',
  'VR' => 'virtual'
}

def prompt_header(window, name)
  header = "The following JSON data summarizes the last #{window.length} clinical encounter(s) for #{name}.
    Each JSON object in the array includes information for a different encounter.

    #{JSON.pretty_unparse(window)}
  "
  header
end

def scribe_prompt(window, name)
    prompt = "#{prompt_header(window, name)}

      Write a medical note for the last encounter in the array.
      
      You may optionally use details and events from previous encounters, if provided, to add historical context to the note.        
      You may date the note according to the encounter_date field.
      You may sign the note using the name provided in the clinician field.
    "
    prompt
end

def examiner_prompt(window, name)
    prompt = "#{prompt_header(window, name)}
      
      #{name} is dead.
      Any compliants or procedures in the summary were known statuses prior to death.

      Write an autopsy report for the last encounter in the array, including a cause of death sequence.
      One cause of death may be indicated in the encounter_reason field.
      A cause of death sequence may include an immediate cause, one or two intermediate causes, and a final underlying cause.

      You may optionally use details and events from previous encounters, if provided, to determine the cause of sequence, and to add historical context to the report.        
      You may date the report according to the encounter_date field.
      You may sign the report using the name provided in the clinician field.
    "
    prompt 
end

scribe = MITRE::LLM.new('llama4')
scribe.set_role('You are an expert medical scribe responsible for writing patient clinical notes.')

examiner = MITRE::LLM.new('llama4')
examiner.set_role('You are an expert medical examiner responsible for conducting autopsies and writing death certificates.')

files = Dir.glob('input/**')
files.each_with_index do |filename, filenum|
  puts "#{filenum}. #{filename}..."

  file = File.open(filename, 'r:UTF-8')
  contents = file.read
  file.close

  bundle = ChattyNotes::Bundle.new(contents)
  context = bundle.patient_context
  fhir_notes = []
  history = []
  last_id = nil

  while !(encounter = bundle.get_resource('Encounter', last_id)).nil?
    last_id = encounter['id']
    encounter_date = encounter['period']
    encounter_class = CLASS_DISPLAY[encounter['class']['code']]
    encounter_type = encounter['type'].first['text']
    encounter_reason = encounter['reasonCode'] ? encounter['reasonCode'].first['coding'].first['display'] : nil
    organization = encounter['serviceProvider']['display']
    clinician = encounter['participant'].first['individual']['display']

    note = nil
    searching = true
    while (searching)
      diagnostic_report = bundle.get_resource('DiagnosticReport', last_id)
      if diagnostic_report.nil?
        searching = false
      else
        last_id = diagnostic_report['id']
        if diagnostic_report['presentedForm'] # assume this is a note, because this field exists
          fhir_notes << diagnostic_report
          note = diagnostic_report['presentedForm'].first['data']
          note = Base64.decode64(note)
          clinician = diagnostic_report['performer'].first['display']
          searching = false
        end
      end
    end
  
    record = {
      "patient" => context['name'],
      "clinician" => clinician,
      "organization" => organization,
      "encounter_date" => encounter_date,
      "encounter_class" => encounter_class,
      "encounter_type" => encounter_type,
      "summary" => note
    }
    record['encounter_reason'] = encounter_reason if encounter_reason

    history << record
  end

  index = 0
  window = []
  history.each_with_index do |record, recordnum|
    puts "  #{filenum}.#{recordnum}. #{record['encounter_date']['start']} #{record['encounter_type']}"
    # puts JSON.pretty_unparse(record)

    window << record
    window.shift if window.length > MAX_HISTORY

    if 'Death Certification' == record['encounter_type']
      prompt = examiner_prompt(window, context['name'])
      reply = examiner.request(prompt)
    else
      prompt = scribe_prompt(window, context['name'])
      reply = scribe.request(prompt)
    end

    output_file = File.open("output/text/#{context['name'].gsub(' ','_')}_#{index}.json", 'w:UTF-8')
    output_file.write(prompt)
    output_file.write("\n\n----------------------------------------------\n\n")
    output_file.write(reply)
    output_file.close

    # rewrite note
    fhir_notes[recordnum]['presentedForm'].first['data'] = Base64.encode64(reply)
    window.last['summary'] = reply
    
    index += 1
  end

  output_filename = filename.split('/').last
  output_file = File.open("output/fhir/#{output_filename}", 'w:UTF-8')
  output_file.write(JSON.pretty_unparse(bundle.json))
  output_file.close
end
puts 'Done.'
