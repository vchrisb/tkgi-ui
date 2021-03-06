from django.shortcuts import render, redirect
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session
from django.contrib.auth.decorators import login_required
from .models import Cluster, Organization
from django.conf import settings
from django.views.generic.edit import CreateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import HttpResponse
from .forms import ClusterForm, OrganizationForm
import json
import kubernetes
from kubernetes.client.rest import ApiException
import urllib.parse

def index(request):
    return render(request, 'webapp/index.html')


### TKGI

@login_required
def cluster(request):
    clusters = Cluster.objects.filter(owner=request.user)
    context = {
        'clusters': clusters,
    }
    return render(request, 'webapp/cluster.html', context)

@login_required
def cluster_kubeconfig(request, pk=None):
    cluster = Cluster.objects.filter(owner=request.user).filter(uuid=pk).first()
    if(cluster):
      if(create_cluster_role_binding(request.user.email, cluster)):
        content = '''apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {0}
    server: https://{1}:{2}
  name: {3}
contexts:
- context:
    cluster: {3}
    user: oidc
  name: {3}
current-context: {3}
kind: Config
users:
- name: oidc
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - oidc-login
      - get-token
      - --oidc-issuer-url={4}
      - --oidc-client-id={5}
      command: kubectl
      env: null
'''.format(settings.TKGI_CA_CERT, cluster.kubernetes_master_ip, cluster.kubernetes_master_port, cluster.name, settings.OIDC_AUTH_ENDPOINT, settings.TKGI_CLUSTER_CLIENT_ID)
        response = HttpResponse(content, content_type='text/plain; charset=utf8')
        response['Content-Disposition'] = 'attachment; filename="kubeconfig"'
        return response
      else:
        print("error creating cluster role binding")
    else:
      print("not found")
    return redirect("cluster")

def create_cluster_role_binding(email, cluster):
    oauth = get_request_object()
    cluster_response = oauth.get("{}/v1/clusters/{}/binds/admin".format(settings.TKGI_API_URL, cluster.name))
    role_binding_name = email.replace('@','__') + '-cluster-admin'
    if(cluster_response.status_code == 200):
      kubeconfig = cluster_response.json()
      # use IP address instead of dns to connect to cluster
      kubeconfig['clusters'][0]['cluster']['server'] = "https://{}:{}".format(cluster.kubernetes_master_ip, cluster.kubernetes_master_port)
      kubernetes.config.load_kube_config_from_dict(kubeconfig)
      with kubernetes.client.ApiClient() as api_client:
        api_instance = kubernetes.client.RbacAuthorizationV1Api(api_client)
        try:
          field_selector = 'metadata.name={}'.format(role_binding_name)
          existing_role_binding = api_instance.list_cluster_role_binding(field_selector=field_selector)  
          if (len(existing_role_binding.items) == 0):
            metadata = kubernetes.client.V1ObjectMeta(name=role_binding_name)
            role_ref = kubernetes.client.V1RoleRef(api_group='rbac.authorization.k8s.io', kind='ClusterRole', name='cluster-admin')
            subject = kubernetes.client.V1Subject(kind='User', name=email)
            role_binding = kubernetes.client.V1RoleBinding(metadata=metadata, role_ref=role_ref, subjects=[subject])
            api_response = api_instance.create_cluster_role_binding(body=role_binding)
            print('Cluster Role Binding {} created!'.format(role_binding_name))
          else:
            print('Cluster Role Binding {} does exist!'.format(role_binding_name))
        except ApiException as e:
          print("Exception when calling RbacAuthorizationV1Api: %s\n" % e)
          
        # clean TKGi role_binding
        try:
          tkgi_role_binding_name = 'pks:{}-cluster-admin'.format(kubeconfig['users'][0]['name'])
          api_response = api_instance.delete_cluster_role_binding(tkgi_role_binding_name)
          print("Deleted TKGi role binding {}".format(tkgi_role_binding_name))
        except ApiException as e:
          print("Exception when calling RbacAuthorizationV1Api->delete_cluster_role_binding: %s\n" % e)
          return False
      return True
    else:
      return False

@login_required
def cluster_refresh(request):
    clusters = Cluster.objects.filter(owner=request.user)
    oauth = get_request_object()

    for cluster in clusters:
      cluster_response = oauth.get("{}/v1/clusters/{}".format(settings.TKGI_API_URL, cluster.name))
      if(cluster_response.status_code == 200):
        current_cluster = cluster_response.json()
        cluster.last_action = current_cluster["last_action"]
        cluster.last_action_state = current_cluster["last_action_state"]
        cluster.last_action_description = current_cluster["last_action_description"]
        cluster.kubernetes_master_host = current_cluster["parameters"]["kubernetes_master_host"]
        cluster.kubernetes_master_port = current_cluster["parameters"]["kubernetes_master_port"]
        cluster.kubernetes_worker_instances = current_cluster["parameters"]["kubernetes_worker_instances"]
        cluster.kubernetes_master_ip = current_cluster["kubernetes_master_ips"][0]
        cluster.k8s_version = current_cluster["k8s_version"]
        cluster.save()
      elif(cluster_response.status_code == 404):
        cluster.delete()
      else:
        print("not found")
  
    return redirect("cluster")

@login_required
def cluster_delete(request, pk=None):
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = ClusterForm(request.POST)
        # check whether it's valid:
        if form.is_valid():
            cluster = Cluster.objects.filter(owner=request.user).filter(uuid=pk).first()
            if(cluster):
              oauth = get_request_object()
              cluster_response = oauth.delete("{}/v1/clusters/{}".format(settings.TKGI_API_URL, cluster.name))
              if(cluster_response.status_code == 204):
                # redirect to a new URL:
                return redirect("cluster_refresh")
              else:
                error = "Error deleting Cluster {}".format(pk)
            else:
              error = "Cluster {} not found or does not belong to user".format(pk)
            return render(request, 'webapp/error.html', {'error': error})
    # if a GET (or any other method) we'll create a blank form
    else:
        form = ClusterForm({'uuid': pk})

    return render(request, 'webapp/cluster_delete.html', {'form': form})

class ClusterCreate(LoginRequiredMixin, CreateView):
    model = Cluster
    fields = ['name']
    template_name = 'webapp/cluster_create.html'
    success_url = reverse_lazy("cluster")

    def form_valid(self, form):
        oauth = get_request_object()
        content = {
          'name': form.instance.name,
          'parameters': {
            'kubernetes_master_host': "{}.{}".format(form.instance.name, settings.TKGI_CLUSTER_BASE_URL),
            'kubernetes_master_port': 8443
          },
          'plan_name': 'small',
          'kubernetes_profile_name': 'oidc-config',
        }
        cluster_response = oauth.post("{}/v1/clusters".format(settings.TKGI_API_URL, ),json=content)
        if(cluster_response.status_code == 202):
          cluster = cluster_response.json()
          form.instance.owner = self.request.user
          current_cluster = cluster_response.json()
          form.instance.last_action = current_cluster["last_action"]
          form.instance.last_action_state = current_cluster["last_action_state"]
          form.instance.last_action_description = current_cluster["last_action_description"]
          form.instance.kubernetes_master_host = current_cluster["parameters"]["kubernetes_master_host"]
          form.instance.kubernetes_master_port = current_cluster["parameters"]["kubernetes_master_port"]
          form.instance.kubernetes_worker_instances = current_cluster["parameters"]["kubernetes_worker_instances"]
          form.instance.uuid = current_cluster["uuid"]
          form.instance.plan_name = current_cluster["plan_name"]
          form.instance.k8s_version = current_cluster["k8s_version"]
          return super().form_valid(form)
        else:
          print(cluster_response.content)
          form.add_error('name', "Failed to create cluster")
          return super().form_invalid(form)

def get_request_object():
    token_url = settings.TKGI_UAA_URL + '/oauth/token'
    client = BackendApplicationClient(client_id=settings.TKGI_CLIENT_ID)
    oauth = OAuth2Session(client=client)
    token = oauth.fetch_token(token_url=token_url, client_id=settings.TKGI_CLIENT_ID, client_secret=settings.TKGI_CLIENT_SECRET)
    return oauth

### TAS

@login_required
def organization(request):
    organizations = Organization.objects.filter(owner=request.user)
    context = {
        'api_url': "https://console.{}".format(settings.TAS_SYS_URL),
        'organizations': organizations,
    }
    return render(request, 'webapp/organization.html', context)

class OrganizationCreate(LoginRequiredMixin, CreateView):
    model = Organization
    fields = ['name']
    template_name = 'webapp/organization_create.html'
    success_url = reverse_lazy("organization")

    def form_valid(self, form):
        oauth = get_request_object_tas()
        
        try: 
          # create user
          user_response = oauth.get("https://uaa.{}/Users?filter=userName+eq+%22{}%22+and+origin+eq+%22{}%22".format(settings.TAS_SYS_URL, self.request.user.email, settings.TAS_ORIGIN))
          if(user_response.status_code == 200):
            user = user_response.json()
            if (user["totalResults"] == 0):
              content = {
                "emails": [
                  {
                    "primary": "true",
                    "value": self.request.user.email
                  }
                ],
                "name": {
                  "familyName": self.request.user.email,
                  "givenName": self.request.user.email
                },
                "origin": settings.TAS_ORIGIN,
                "password": "[PRIVATE DATA HIDDEN]",
                "userName": self.request.user.email
              }
              user_response = oauth.post("https://uaa.{}/Users".format(settings.TAS_SYS_URL, settings.TAS_ORIGIN),json=content)
              if(user_response.status_code != 201):
                Exception('Could not create User')
          else:
            Exception('Response for getting users not 200')

          # create organization
          content = {
          'name': form.instance.name,
          }
          org_response = oauth.post("https://api.{}/v3/organizations".format(settings.TAS_SYS_URL),json=content)
          print(org_response.content)
          if(org_response.status_code != 201):
            Exception('Could not create organization')
          org_guid = org_response.json()["guid"]
          # create role
          content = {
                "type": "organization_manager",
                "relationships": {
                  "user": {
                    "data": {
                      "username": self.request.user.email
                    }
                  },
                  "organization": {
                    "data": {
                      "guid": org_guid
                    }
                  }
                }
          }
          role_response = oauth.post("https://api.{}/v3/roles".format(settings.TAS_SYS_URL),json=content)
          if(role_response.status_code != 201):
            Exception('Could not create role')

          form.instance.owner = self.request.user
          form.instance.uuid = org_guid
          return super().form_valid(form)
        except:
          print("Error in OrganizationCreate")
          raise
          form.add_error('name', "Failed to create organization")
          return super().form_invalid(form)

@login_required
def organization_delete(request, pk=None):
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = OrganizationForm(request.POST)
        # check whether it's valid:
        if form.is_valid():
            organization = Organization.objects.filter(owner=request.user).filter(uuid=pk).first()
            if(organization):
              oauth = get_request_object_tas()
              org_response = oauth.delete("https://api.{}/v3/organizations/{}".format(settings.TAS_SYS_URL, organization.uuid))
              if(org_response.status_code == 202):
                # delete in db
                organization.delete()
                # redirect to a new URL:
                return redirect("organization")
              else:
                error = "Error deleting Organization {}".format(pk)
            else:
              error = "Organization {} not found or does not belong to user".format(pk)
            return render(request, 'webapp/error.html', {'error': error})
    # if a GET (or any other method) we'll create a blank form
    else:
        form = OrganizationForm({'uuid': pk})

    return render(request, 'webapp/organization_delete.html', {'form': form})

def get_request_object_tas():
    token_url = "https://uaa.{}/oauth/token".format(settings.TAS_SYS_URL)
    client = BackendApplicationClient(client_id=settings.TAS_CLIENT_ID)
    oauth = OAuth2Session(client=client)
    token = oauth.fetch_token(token_url=token_url, client_id=settings.TAS_CLIENT_ID, client_secret=settings.TAS_CLIENT_SECRET)
    return oauth