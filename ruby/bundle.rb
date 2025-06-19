require 'json'
require 'date'

module ChattyNotes
  class Bundle
    attr_accessor :raw_content
    attr_accessor :json
    attr_accessor :active_conditions

    def initialize(raw_content)
      @raw_content = raw_content
      @json = JSON.parse(@raw_content)
    end

    def get_resource(resource_type, last_id = nil)
      @json['entry'].each do |entry|
        resource = entry['resource']
        if last_id.nil?
          return resource if resource_type == resource['resourceType']
        else
          last_id = nil if last_id == resource['id']
        end
      end
      nil
    end

    def get_name(patient = {})
      name = patient['name'].find {|n| n['use'] == 'official'}
      prefix = name['prefix'] ? name['prefix'].first : nil
      given = name['given'] ? name['given'].join(' ') : nil
      family = name['family'] ? name['family'] : nil
      suffix = name['suffix'] ? name['suffix'].first : nil
      [prefix, given, family, suffix].compact.join(' ')
    end

    def get_race(patient = {})
      race = patient['extension'].find {|x| x['url'].include?('us-core-race')}
      race = race['extension'].find {|x| x['valueString']}['valueString']
      race
    end

    def get_ethnicity(patient = {})
      ethnicity = patient['extension'].find {|x| x['url'].include?('us-core-ethnicity')}
      ethnicity = ethnicity['extension'].find {|x| x['valueString']}['valueString']
      ethnicity
    end

    def patient_context(context = {})
      resource = get_resource('Patient')
      context['name'] = get_name(resource)
      context['birth_date'] = Date.parse(resource['birthDate'])
      # context['age'] = 55
      context['race'] = get_race(resource)
      context['ethnicity'] = get_ethnicity(resource)
      context['gender'] = resource['gender']
      # context['reason'] = 'malaise'
      # context['medications'] = ['aspirin', 'ibuprofen']
      # context['procedures'] = ['physical exam', 'cognitive assessment']
      # context['procedure'] = 'physical therapy'
      # context['immunizations'] = nil
      # context['encounter_type'] = nil
      # context['conditions'] = nil
      context
    end

    def save(filename)
      file = File.open(filename, 'w:UTF-8')
      file.write(JSON.pretty_unparse(@json))
      file.close
    end
  end
end