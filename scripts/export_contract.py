"""Export the designed v1 HTTP contract; this does not start a service."""
import json
from pathlib import Path
import sys

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from scriptalocal import contracts as c


def document():
    classes = [value for value in vars(c).values()
               if isinstance(value,type) and issubclass(value,BaseModel)
               and value not in {BaseModel,c.Contract}]
    _, definitions = models_json_schema([(cls,'validation') for cls in classes],
                                        ref_template='#/components/schemas/{model}')
    schemas = definitions['$defs']
    def response(name, description='Successful response'):
        return {'description':description,'content':{'application/json':{
            'schema':{'$ref':'#/components/schemas/'+name}}}}
    errors = {str(status):response('ErrorResponse',description) for status,description in
              [(401,'Credentials required'),(403,'Insufficient capability'),
               (404,'Resource not found'),(409,'Conflict or result not ready'),
               (413,'Upload or dimensions exceed configured limit'),
               (415,'Unsupported format or multiple frames'),(422,'Invalid request'),
               (429,'Queue or concurrency limit reached'),(500,'Unexpected service error'),(503,'Required component unavailable')]}
    job_parameter = {'name':'job_id','in':'path','required':True,
                     'schema':{'type':'string','format':'uuid'}}
    paths = {}
    def add(path, method, name, output=None, status=200, body=None, parameters=None):
        operation = {'operationId':name,'summary':name.replace('_',' '),
                     'responses':{str(status):response(output) if output else {'description':'Deleted'},
                                  **errors}}
        if '{job_id}' in path:
            operation['parameters']=[job_parameter]
        if parameters:
            operation.setdefault('parameters',[]).extend(parameters)
        if body:
            operation['requestBody']={'required':True,'content':{'application/json':{
                'schema':{'$ref':'#/components/schemas/'+body}}}}
        paths.setdefault(path,{})[method]=operation
        return operation
    live = add('/health/live','get','liveness','Health')
    live['security']=[]
    add('/health/ready','get','readiness','Health')
    add('/v1/models','get','installed_models','ModelInventory')
    upload=add('/v1/jobs','post','create_job','JobAccepted',202,parameters=[{
        'name':'Idempotency-Key','in':'header','required':False,
        'description':'Same caller, key, image bytes and canonical options replay the same job; different content returns 409.',
        'schema':{'type':'string','minLength':1,'maxLength':128,'pattern':'^[!-~]+$'}}])
    upload['requestBody']={'required':True,'content':{'multipart/form-data':{'schema':{
        'type':'object','additionalProperties':False,'required':['image'],
        'properties':{'image':{'type':'string','format':'binary'},
                      'options':{'type':'string','description':'JSON-encoded JobOptions object; omitted means configured defaults.',
                                 'contentMediaType':'application/json',
                                 'contentSchema':{'$ref':'#/components/schemas/JobOptions'}}}}}}}
    add('/v1/jobs/{job_id}','get','job_status','JobStatus')
    result=add('/v1/jobs/{job_id}/result','get','job_result','JobResult',parameters=[
        {'name':'revision','in':'query','required':False,'schema':{'type':'integer','minimum':1}}])
    result['description']='Returns the latest completed result when the current job succeeds. An explicit revision retrieves immutable completed history even while a newer revision is queued, cancelled or failed.'
    add('/v1/jobs/{job_id}/cancel','post','cancel_job','JobStatus')
    add('/v1/jobs/{job_id}','delete','delete_job',status=204)
    revision=add('/v1/jobs/{job_id}/revisions','post','revise_job','JobAccepted',202,body='RevisionInput')
    revision['description']='Creates a new revision without changing prior raw output. Geometry changes queue downstream processing; text-only revisions queue assembly. Stale expected_revision returns 409.'
    inventory=add('/v1/jobs/{job_id}/artifacts','get','list_artifacts','ArtifactInventory',parameters=[
        {'name':'after','in':'query','required':False,'schema':{'type':'string','format':'uuid'}},
        {'name':'limit','in':'query','required':False,'schema':{'type':'integer','minimum':1,'maximum':200,'default':100}}])
    inventory['description']='Returns a job-wide original descriptor and UUID-ordered committed artifacts. Follow next_cursor as after. Repeat enumeration after completion if stages or revisions were changing during pagination. Filesystem paths are never returned.'
    artifact=add('/v1/jobs/{job_id}/artifacts/{artifact_id}','get','retrieve_artifact',parameters=[{
        'name':'artifact_id','in':'path','required':True,'schema':{'type':'string','format':'uuid'}}])
    artifact['responses']['200']={'description':'Authorized job artifact; path is never supplied by the caller.',
                                 'content':{mime:{'schema':{'type':'string','format':'binary'}} for mime in ['application/octet-stream','image/png','image/jpeg','image/tiff']}}
    return {'openapi':'3.1.0','info':{'title':'ScriptaLocal API','version':'1.0.0-design',
             'description':'Version 1 contract with an API prototype, immutable revision history and queued corrections. All geometry uses original encoded pixel edges.'},
            'security':[{'BearerAuth':[]}], 'paths':paths,
            'components':{'schemas':schemas,'securitySchemes':{'BearerAuth':{
                'type':'http','scheme':'bearer','description':'Server-issued token with read, write or delete capability; never embed credentials in browser bundles.'}}}}


if __name__=='__main__':
    target=Path(sys.argv[1] if len(sys.argv)>1 else 'docs/api/openapi.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(document(),indent=2)+'\n')
