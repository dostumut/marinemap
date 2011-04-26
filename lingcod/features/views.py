from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext, Context
from django.template import loader, TemplateDoesNotExist
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Group
from lingcod.features.models import Feature
from lingcod.features import user_sharing_groups
from lingcod.common.utils import get_logger
from lingcod.common import default_mimetypes as mimetypes
from lingcod.features import workspace_json, get_feature_by_uid
from django.template.defaultfilters import slugify
from lingcod.features.models import SpatialFeature
from django.core.urlresolvers import reverse
from django.views.decorators.cache import cache_page
logger = get_logger()

def get_object_for_editing(request, uid, target_klass=None):
    """
    Return the specified instance by uid for editing.
    If a target_klass is provided, uid will be checked for consistency.
    If the request has no logged-in user, a 401 Response will be returned. If 
    the item is not found, a 404 Response will be returned. If the user is 
    not authorized to edit the item (not the owner or a staff user), a 403 Not
    Authorized Response will be returned.
    
    usage:

    instance = get_object_for_editing(request, 'mlpa_mpa_12', target_klass=Mpa)
    if isinstance(instance, HttpResponse):
        return instance

    """
    if target_klass and not target_klass.model_uid() in uid:
        return HttpResponse("Target class %s doesn't match the provided uid %s" % 
                            (target_klass, uid),
                            status=401)
    try:
        instance = get_feature_by_uid(uid)
    except ValueError:
        return HttpResponse("Uid not valid: %s" % uid, status=401)
    except:
        return HttpResponse("Feature not found - %s" % uid, status=404)

    if not request.user.is_authenticated():
        return HttpResponse('You must be logged in.', status=401)
    # Check that user owns the object or is staff
    if not request.user.is_staff and request.user != instance.user:
        return HttpResponseForbidden(
            'You do not have permission to modify this object.')
    return instance

def get_object_for_viewing(request, uid, target_klass=None):
    """
    Return the specified instance by uid for viewing.
    If a target_klass is provided, uid will be checked for consistency.
    If the request has no authenticated user, a 401 Response will be returned.
    If the item is not found, a 404 Response will be returned. If the user is 
    not authorized to view the item (not the owner or part of a group the item
    is shared with), a 403 Not Authorized Response will be returned.

    usage:

    instance = get_object_for_viewing(request, 'mlpa_mpa_12', target_klass=Mpa)
    if isinstance(instance, HttpResponse):
        return instance

    """
    if target_klass and not target_klass.model_uid() in uid:
        return HttpResponse("Target class %s doesn't match the provided uid %s" % 
                            (target_klass, uid),
                            status=401)
    try:
        instance = get_feature_by_uid(uid)
    except ValueError:
        return HttpResponse("Uid not valid: %s" % uid, status=401)
    except:
        return HttpResponse("Feature not found - %s" % uid, status=404)

    viewable, response = instance.is_viewable(request.user) 
    if viewable:
        return instance
    else:
        return response

# RESTful Generic Views

def handle_link(request, uids, link=None):
    """
    Handles all requests to views setup via features.register using Link 
    objects.
    
    Assuming a valid request, this generic view will call the view specified 
    by the link including an instance or instances argument containing the 
    relavent Feature(s).

    If the incoming request is invalid, any one of the following errors may be
    returned:
    
    401: login required
    403: user does not have permission (not admin user or doesn't own object 
         to be edited)
    404: feature(s) could not be found
    400: requested for feature classes not supported by this view
    5xx: server error
    """
    if link is None:
        raise Exception('handle_link configured without link kwarg!')
    uids = uids.split(',')
    # check that the number of instances matches the link.select property
    if len(uids) > 1 and link.select is 'single':
        # too many
        return HttpResponse(
            'Not Supported Error: Requested %s for multiple instances' % (
            link.title, ), status=400)
    singles = ('single', 'multiple single', 'single multiple')
    if len(uids) is 1 and link.select not in singles:
        # not enough
        return HttpResponse(
            'Not Supported Error: Requested %s for single instance' % (
            link.title, ), status=400)
    instances = []
    for uid in uids:
        if link.rel == 'edit':
            if link.method.lower() == 'post' and request.method == 'GET':
                resp = HttpResponse('Invalid Method', status=405)
                resp['Allow'] = 'POST'
                return resp
            if link.edits_original is False:
                # users who can view the object can then make copies
                inst = get_object_for_viewing(request, uid)
            else:
                inst = get_object_for_editing(request, uid)
        else:
            inst = get_object_for_viewing(request, uid)

        if isinstance(inst, HttpResponse):
            return inst
        else:
            instances.append(inst)
    for instance in instances:
        if link.generic and instance.__class__ not in link.models:
            return HttpResponse(
                'Not Supported Error: Requested for "%s" feature class. This \
generic link only supports requests for feature classes %s' % (
                instance.__class__.__name__, 
                ', '.join([m.__name__ for m in link.models])), status=400)
                
    if link.select is 'single':
        return link.view(request, instances[0], **link.extra_kwargs)
    else:
        return link.view(request, instances, **link.extra_kwargs)
    
def delete(request, model=None, uid=None):
    """
    When calling, provide the request object, reference to the resource
    class, and the primary key of the object to delete.

    Possible response codes:
    
    200: delete operation successful
    401: login required
    403: user does not have permission (not admin user or doesn't own object)
    404: resource for deletion could not be found
    5xx: server error
    """
    if model is None:
        return HttpResponse('Model not specified in feature urls', status=500)
    if request.method == 'DELETE':
        if model is None or uid is None:
            raise Exception('delete view not configured properly.')
        instance = get_object_for_editing(request, uid, target_klass=model) 
        if isinstance(instance, HttpResponse):
            # get_object_for_editing is trying to return a 404, 401, or 403
            return instance
        instance.delete()
        return HttpResponse('{"status": 200}')
    else:
        return HttpResponse('DELETE http method must be used to delete', 
            status=405)

def multi_delete(request, instances):
    """ 
    Generic view to delete multiple instances 
    """
    deleted = []
    if request.method == 'DELETE':
        for instance in instances:
            uid = instance.uid
            instance.delete()
            deleted.append(uid)
        return HttpResponse('{"status": 200}')
    else:
        return HttpResponse('DELETE http method must be used to delete', 
                status=405)
    

def create(request, model, action):
    """
    When calling, provide the request object and a ModelForm class
            
        POST:   Create a new instance from filled out ModelForm

            201: Created. Response body includes representation of resource
            400: Validation error. Response body includes form. Form should
                be displayed back to the user for correction.
            401: Not logged in.
            5xx: Server error.
    """
    config = model.get_options()
    form_class = config.get_form_class()
    if not request.user.is_authenticated():
        return HttpResponse('You must be logged in.', status=401)    
    title = 'New %s' % (config.slug, )
    if request.method == 'POST':
        values = request.POST.copy()
        values.__setitem__('user', request.user.pk)
        if request.FILES:
            form = form_class(values, request.FILES, label_suffix='')
        else:
            form = form_class(values, label_suffix='')
        if form.is_valid():
            m = form.save()
            m.save()
            response = HttpResponse("""{
                "status": 201,
                "Location": "%s",
                "X-MarineMap-Select": "%s",
                "X-MarineMap-Show": "%s"
            }""" % (m.get_absolute_url(), m.uid, m.uid), status=201)
            response['X-MarineMap-Select'] = m.uid
            response['X-MarineMap-Show'] = m.uid
            response['Location'] = m.get_absolute_url()
            return response
        else:
            context = config.form_context
            context.update({
                'form': form,
                'title': title,
                'action': action,
                'is_ajax': request.is_ajax(),
                'MEDIA_URL': settings.MEDIA_URL,
                'is_spatial': issubclass(model, SpatialFeature),
            })
            context = decorate_with_manipulators(context, form_class)
            c = RequestContext(request, context)
            t = loader.get_template(config.form_template)
            return HttpResponse(t.render(c), status=400)
    else:
        return HttpResponse('Invalid http method', status=405)

def create_form(request, model, action=None):
    """
    Serves a form for creating new objects
    
    GET only
    """
    config = model.get_options()
    form_class = config.get_form_class()
    if action is None:
        raise Exception('create_form view is not configured properly.')
    if not request.user.is_authenticated():
        return HttpResponse('You must be logged in.', status=401)
    title = 'New %s' % (config.verbose_name)
    context = config.form_context
    if request.method == 'GET':
        context.update({
            'form': form_class(label_suffix=''),
            'title': title,
            'action': action,
            'is_ajax': request.is_ajax(),
            'MEDIA_URL': settings.MEDIA_URL,
            'is_spatial': issubclass(model, SpatialFeature),
        })
        context = decorate_with_manipulators(context, form_class)
        return render_to_response(config.form_template, context)
    else:
        return HttpResponse('Invalid http method', status=405)

def update_form(request, model, uid):
    """
    Returns a form for editing features
    """
    instance = get_object_for_editing(request, uid, target_klass=model)
    if isinstance(instance, HttpResponse):
        # get_object_for_editing is trying to return a 404, 401, or 403
        return instance
    try:
        instance.get_absolute_url()
    except:
        raise Exception(
            'Model to be edited must have get_absolute_url defined.')
    try:
        instance.name
    except:
        raise Exception('Model to be edited must have a name attribute.')

    config = model.get_options()
    if request.method == 'GET':
        form_class = config.get_form_class()
        form = form_class(instance=instance, label_suffix='')
        context = config.form_context
        context.update({
            'form': form,
            'title': "Edit '%s'" % (instance.name, ),
            'action': instance.get_absolute_url(),
            'is_ajax': request.is_ajax(),
            'MEDIA_URL': settings.MEDIA_URL,
            'is_spatial': issubclass(model, SpatialFeature),
        })
        context = decorate_with_manipulators(context, form_class)
        return render_to_response(config.form_template, context)
    else:
        return HttpResponse('Invalid http method', status=405)        

def update(request, model, uid):
    """
        When calling, provide the request object, a model class, and the
        primary key of the instance to be updated.
                
            POST: Update instance.
            
                possible response codes:
                
                200: OK. Object updated and in response body.
                400: Form validation error. Present form back to user.
                401: Not logged in.
                403: Forbidden. User is not staff or does not own object.
                404: Instance for uid not found.
                5xx: Server error.
    """
    config = model.get_options()
    instance = get_object_for_editing(request, uid, target_klass=model)
    if isinstance(instance, HttpResponse):
        # get_object_for_editing is trying to return a 404, 401, or 403
        return instance
    try:
        instance.get_absolute_url()
    except:
        raise Exception('Model must have get_absolute_url defined.')
    try:
        instance.name
    except:
        raise Exception('Model to be edited must have a name attribute.')
        
    if request.method == 'POST':
        values = request.POST.copy()
        # Even if request.user is different (ie request.user is staff)
        # user is still set to the original owner to prevent staff from 
        # 'stealing' 
        values.__setitem__('user', instance.user.pk)
        form_class = config.get_form_class()
        if request.FILES:
            form = form_class(
                values, request.FILES, instance=instance, label_suffix='')
        else:
            form = form_class(values, instance=instance, label_suffix='')
        if form.is_valid():
            m = form.save()
            m.save()
            response = HttpResponse("""{
                "status": 200,
                "X-MarineMap-Select": "%s",
                "X-MarineMap-Show": "%s"
            }""" % (m.uid, m.uid), status=200)
            response['X-MarineMap-Select'] = m.uid
            response['X-MarineMap-Show'] = m.uid
            return response
        else:
            context = config.form_context
            context.update({
                'form': form,
                'title': "Edit '%s'" % (instance.name, ),
                'action': instance.get_absolute_url(),
                'is_ajax': request.is_ajax(),
                'MEDIA_URL': settings.MEDIA_URL,
                'is_spatial': issubclass(model, SpatialFeature),
            })
            context = decorate_with_manipulators(context, form_class)
            c = RequestContext(request, context)
            t = loader.get_template(config.form_template)
            return HttpResponse(t.render(c), status=400)
    else:
        return HttpResponse("""Invalid http method. 
        Yes we know, PUT is supposed to be used rather than POST, 
        but it was much easier to implement as POST :)""", status=405)
        
    
def resource(request, model=None, uid=None):
    """
    Provides a resource for a django model that can be utilized by the 
    lingcod.features client module.
    
    Implements actions for the following http actions:
    
        POST:   Update an object
        DELETE: Delete it
        GET:    Provide a page representing the model. For MPAs, this is the 
                MPA attributes screen. The marinemap client will display this
                page in the sidebar whenever the object is brought into focus. 

                To implement GET, this view needs to be passed a view function
                that returns an HttpResponse or a template can be specified
                that will be passed the instance and an optional extra_context
        
    Uses lingcod.features.views.update and lingcod.feature.views.delete
    """
    if model is None:
        return HttpResponse('Model not specified in feature urls', status=500)
    config = model.get_options()
    if request.method == 'DELETE':
        return delete(request, model, uid)
    elif request.method == 'GET':
        instance = get_object_for_viewing(request, uid, target_klass=model)
        if isinstance(instance, HttpResponse):
            # Object is not viewable so we return httpresponse
            # should contain the appropriate error code
            return instance
            
        t = config.get_show_template()
        context = config.show_context
        context.update({
            'instance': instance,
            'MEDIA_URL': settings.MEDIA_URL,
            'is_ajax': request.is_ajax(),
            'template': t.name,
        })

        return HttpResponse(t.render(RequestContext(request, context)))
    elif request.method == 'POST':
        return update(request, model, uid)
        
def form_resources(request, model=None, uid=None):
    if model is None:
        return HttpResponse('Model not specified in feature urls', status=500)
    if request.method == 'POST':
        if uid is None:
            return create(request, model, request.build_absolute_uri())
        else:
            return HttpResponse('Invalid http method', status=405)        
    elif request.method == 'GET':
        if uid is None:
            # Get the create form
            return create_form(
                request, 
                model,
                action=request.build_absolute_uri())
        else:
            # get the update form
            return update_form(request, model, uid)
    else:
        return HttpResponse('Invalid http method', status=405)        

from lingcod.manipulators.manipulators import get_manipulators_for_model
from django.utils import simplejson

# TODO: Refactor this so that it is part of Feature.Options.edit_context
def decorate_with_manipulators(extra_context, form_class):
    try:
        extra_context['json'] = simplejson.dumps(get_manipulators_for_model(form_class.Meta.model))
    except:
        extra_context['json'] = False
    return extra_context

def copy(request, instances):
    """
    Generic view that can be used to copy any feature classes. Supports 
    requests referencing multiple instances.
    
    To copy, this view will call the copy() method with the request's user as
    it's sole argument. The Feature base class has a generic copy method, but
    developers can override it. A poorly implemented copy method that does not
    return the copied instance will raise an exception here.
    
    This view returns a space-delimited list of the Feature uid's for 
    selection in the user-interface after this operation via the 
    X-MarineMap-Select response header.
    """
    copies = []
    copied_uids = ' '.join([i.uid for i in instances])
    for instance in instances:
        copy = instance.copy(request.user)
        if not copy or not isinstance(copy, Feature):
            raise Exception('copy method on feature class %s did not return \
Feature instance.' % (instance.__class__.__name__, ))
        copies.append(copy)
    links = ', '.join(['<a href="%s">%s</a>' % (
        i.get_absolute_url(), i.name) for i in copies])

    uids = ' '.join([i.uid for i in copies])
    response = HttpResponse("""{
        "status": 201,
        "X-MarineMap-Select": "%s",
        "X-MarineMap-UnToggle": "%s"
    }""" % (uids, copied_uids), status=201)
    response['X-MarineMap-Select'] = uids
    response['X-MarineMap-UnToggle'] = copied_uids
    return response

def kml(request, instances):
    return kml_core(request, instances, kmz=False)

def kmz(request, instances):
    return kml_core(request, instances, kmz=True)

def kml_core(request, instances, kmz):
    """
    Generic view for KML representation of feature classes. 
    Can be overridden in options but this provided a default.
    """
    from lingcod.kmlapp.views import get_data_for_feature, get_styles, create_kmz 
    from django.template.loader import get_template
    from lingcod.common import default_mimetypes as mimetypes

    user = request.user
    try:
        session_key = request.COOKIES['sessionid']
    except:
        session_key = 0

    # Get features, collection from instances
    features = []
    collections = []

    # If there is only a single instance with a kml_full property,
    # just return the contents verbatim
    if len(instances) == 1:
        from lingcod.common.utils import is_text
        filename = slugify(instances[0].name)
        try:
            kml = instances[0].kml_full
            response = HttpResponse()
            if is_text(kml) and kmz:
                # kml_full is text, but they want as KMZ
                kmz = create_kmz(kml, 'mpa/doc.kml')
                response['Content-Type'] = mimetypes.KMZ
                response['Content-Disposition'] = 'attachment; filename=%s.kmz' % filename
                response.write(kmz)
                return response
            elif is_text(kml) and not kmz:
                # kml_full is text, they just want kml
                response['Content-Type'] = mimetypes.KML
                response['Content-Disposition'] = 'attachment; filename=%s.kml' % filename
                response.write(kml)
                return response
            else:
                # If the kml_full returns binary, always return kmz
                # even if they asked for kml
                response['Content-Type'] = mimetypes.KMZ
                response['Content-Disposition'] = 'attachment; filename=%s.kmz' % filename
                response.write(kml) # actually its kmz but whatevs
                return response
        except AttributeError:
            pass

    for instance in instances:
        f, c = get_data_for_feature(user,instance.uid)
        features.extend(f)
        collections.extend(c)

    if not features and isinstance(collections, HttpResponse):
        return collections # We got an http error going on

    styles = get_styles(features,collections)

    t = get_template('kmlapp/base.kml')
    context = Context({
                'user': user, 
                'features': features, 
                'collections': collections,
                'use_network_links': False, 
                'request_path': request.path, 
                'styles': styles,
                'session_key': session_key,
                'shareuser': None,
                'sharegroup': None,
                'feature_id': None,
                })
    kml = t.render(context)
    response = HttpResponse()
    filename = '_'.join([slugify(i.name) for i in instances])

    if kmz:
        kmz = create_kmz(kml, 'mpa/doc.kml')
        response['Content-Type'] = mimetypes.KMZ
        response['Content-Disposition'] = 'attachment; filename=%s.kmz' % filename
        response.write(kmz)
    else:
        response['Content-Type'] = mimetypes.KML
        response['Content-Disposition'] = 'attachment; filename=%s.kml' % filename
        response.write(kml)
        response.write('\n')
    return response

def share_form(request,model=None, uid=None):
    """
    Generic view for showing the sharing form for an object

        POST:   Update the sharing status of an object
        GET:    Provide an html form for selecting groups
                to which the feature will be shared.
    """
    if model is None:
        return HttpResponse('Model not specified in feature urls', status=500)
    if uid is None:
        return HttpResponse('Instance UID not specified', status=500)

    obj = get_object_for_editing(request, uid, target_klass=model)

    if isinstance(obj, HttpResponse):
        return obj
    if not isinstance(obj, Feature):
        return HttpResponse('Instance is not a Feature', status=500)

    obj_type_verbose = obj._meta.verbose_name

    if request.method == 'GET':
        # Display the form
        # Which groups is this object already shared to?
        already_shared_groups = obj.sharing_groups.all()

        # Get a list of user's groups that have sharing permissions 
        groups = user_sharing_groups(request.user)

        return render_to_response('sharing/share_form.html', {'groups': groups,
            'already_shared_groups': already_shared_groups, 'obj': obj,
            'obj_type_verbose': obj_type_verbose,  'user':request.user, 
            'MEDIA_URL': settings.MEDIA_URL,
            'action': request.build_absolute_uri()}) 

    elif request.method == 'POST':
        group_ids = [int(x) for x in request.POST.getlist('sharing_groups')]
        groups = Group.objects.filter(pk__in=group_ids)

        try:
            obj.share_with(groups)
            response = HttpResponse("""{
                "status": 200,
                "X-MarineMap-Select": "%s"
            }""" % (obj.uid, ), status=200)
            response['X-MarineMap-Select'] = obj.uid
            return response
        except Exception as e:
            return HttpResponse(
                    'Unable to share objects with those specified groups: %r.' % e, 
                    status=500)

    else:
        return HttpResponse( "Received unexpected " + request.method + 
                " request.", status=400 )

def manage_collection(request, action, uids, collection_model, collection_uid):
    config = collection_model.get_options()
    collection_instance = get_object_for_editing(request, collection_uid,
            target_klass=collection_model)
    if isinstance(collection_instance, HttpResponse):
        return instance
        
    if request.method == 'POST':
        uids = uids.split(',')
        instances = []
        for uid in uids:
            inst = get_object_for_editing(request, uid)

            if isinstance(inst, HttpResponse):
                return inst
            else:
                instances.append(inst)

        if action == 'remove':
            for instance in instances:
                instance.remove_from_collection()
        elif action == 'add':
            for instance in instances:
                instance.add_to_collection(collection_instance)
        else:
            return HttpResponse("Invalid action %s." % action, status=500)
        
        response = HttpResponse("""{
            "status": 200,
            "X-MarineMap-Select": "%s",
            "X-MarineMap-Parent-Hint": "%s"
        }""" % (' '.join(uids), collection_instance.uid), status=200)
        response['X-MarineMap-Select'] = ' '.join(uids)
        response["X-MarineMap-Parent-Hint"] = ' '.join(uids)
        return response
    else:
        return HttpResponse("Invalid http method.", status=405)

@cache_page(60 * 60)
def workspace(request, is_owner):
    user = request.user
    if request.method == 'GET':
        if user.is_anonymous() and is_owner:
            return HttpResponse("Anonymous user can't access workspace as owner", status=403)
        res = HttpResponse(workspace_json(user, is_owner), status=200)
        res['Content-Type'] = mimetypes.JSON 
        return res
    else:
        return HttpResponse("Invalid http method.", status=405)

@cache_page(60 * 60)
def feature_tree_css(request):
    from lingcod.features import registered_models
    if request.method == 'GET':
        styles = []
        for model in registered_models:
            try:
                css = model.css()
                styles.append(css)
            except:
                logger.ERROR("Something is wrong with %s.css() class method" % model)
                pass

        res = HttpResponse('\n'.join(styles), status=200)
        res['Content-Type'] = 'text/css' 
        return res
    else:
        return HttpResponse("Invalid http method.", status=405)
