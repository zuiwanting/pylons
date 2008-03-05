"""Render functions and helpers, including legacy Buffet implementation

Render functions and helpers
============================

``pylons.templating`` includes several basic render functions, 
``render_mako`` and ``render_genshi`` that render templates from the
file-system with the assumption that variables intended for the 
template will be attached to ``tmpl_context`` (hereafter referred to
by its short name of ``c`` which it is commonly imported as).

The default render functions work with the template language loader
object that is setup on the ``g`` globals object in the project's
``config/environment.py``.

Usage
-----

Generally, one of the render functions will be imported in the 
controller. Variables intended for the template are attached to the
``c`` object.

.. tip ::
``tmpl_context`` (template context) is abbreviated to ``c`` instead of
its full name since it will likely be used extensively and it's much
faster to use ``c``. Of course, for users that can't tolerate 
one-letter variables, feel free to not import ``tmpl_context`` as 
``c`` since both names are available in templates as well.

Example of rendering a template with some variables:

.. sourcecode:: python
    from pylons import tmpl_context as c
    from pylons.templating import render_mako as render

    from sampleproject.lib.base import BaseController


    class SampleController(BaseController):

        def index(self):
            c.first_name = "Joe"
            c.last_name = "Smith"
            return render('/some/template.html')

And the accompanying Mako template:

.. sourcecode:: mako
    Hello ${c.first name}, I see your lastname is ${c.last_name}!

Your controller will have additional default imports for commonly used
functions.

Template Globals
----------------

Templates rendered in Pylons should include the default Pylons globals
as the ``render_mako`` and ``render_genshi`` functions. The full list
of Pylons globals that are included the template's namespace are:

- c -- Template context object
- tmpl_context -- Template context object
- config -- Pylons configuration object (acts as a dict)
- g -- Project application globals object
- h -- Project helpers module reference
- request -- Pylons request object for this request
- response -- Pylons response object for this request
- translator -- Gettext translator object configured for current locale
- ungettext -- Unicode capable version of gettext's ngettext function
  (handles plural translations)
- _ -- Unicode capable gettext translate function
- N_ -- gettext no-op function to mark a string for translation, but
  doesn't actually translate

Writing your own render function
--------------------------------

The default render function doesn't fully expose template language
capabilities as various template languages have different sets of
functionality. If you need to use specific features in Genshi, or
another template language entirely, a custom render function should
be made.

Two helper functions for use with the render function make it easy to
include the common Pylons globals that are useful in a template as well
as enabling easy use of cache capabilities. The ``pylons_globals`` and
``cached_template`` functions can be used if desired.

Generally, the custom render function should reside in your project's
``lib/`` directory, probably in ``base.py``.

Here's a sample Genshi render function as it would look in a project's
``lib/base.py`` that doesn't fully render the result to a string, and
rather than use ``c`` assumes that a dict is passed in to be used in
the templates global namespace. It also returns a Genshi stream instead
of the rendered string.

.. sourcecode:: python
    from pylons.templating import pylons_globals
    
    def render(template_name, tmpl_vars):
        # First, get the globals
        globs = pylons_globals()

        # Update the passed in vars with the globals
        tmpl_vars.update(globs)
        
        # Grab a template reference
        template = globs['g'].genshi_loader.load(template_name)
        
        # Render the template
        return template.generate(**tmpl_vars)

In 6 short lines of Python code, you have a custom render function that
makes it easy to get to the features of the template language you need.

.. note::
    Importing the Pylons globals also makes it easy to get to ``g`` 
    which is where your template language's persistent template loader
    should be (if that applies to your chosen template language).

Legacy Buffet templating plugin and render functions
====================================================

The Buffet object is styled after the original Buffet module that implements
template language neutral rendering for CherryPy. This version of Buffet also
contains caching functionality that utilizes 
`Beaker middleware <http://beaker.groovie.org/>`_ to provide template language
neutral caching functionality.

A customized version of 
`BuffetMyghty <http://projects.dowski.com/projects/buffetmyghty>`_ is included
that provides a template API hook as the ``pylonsmyghty`` engine. This version
of BuffetMyghty disregards some of the TurboGears API spec so that traditional
Myghty template names can be used with ``/`` and file extensions.

The render functions are intended as the primary user-visible rendering 
commands and hook into Buffet to make rendering content easy.
"""
import logging
import os
import warnings

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
import pkg_resources

import pylons

__all__ = ['Buffet', 'MyghtyTemplatePlugin', 'render', 'render_genshi', 
           'render_mako', 'render_response']

PYLONS_VARS = ['c', 'config', 'g', 'h', 'render', 'request', 'session',
               'translator', 'ungettext', '_', 'N_']

log = logging.getLogger(__name__)

def pylons_globals():
    """Create and return a dictionary of global Pylons variables
    
    Render functions should call this to retrieve a list of global
    Pylons variables that should be included in the global template
    namespace if possible.
    
    Pylons variables that are returned in the dictionary:
        c, g, h, _, N_, config, request, response, translator,
        ungettext
    
    """
    conf = pylons.config._current_obj()
    c = pylons.tmpl_context._current_obj()
    pylons_vars = dict(
        c=c,
        tmpl_context=c,
        config=conf,
        g=conf['pylons.g'],
        h=conf.get('pylons.h') or pylons.h._current_obj(),
        request=pylons.request._current_obj(),
        response=pylons.response._current_obj(),
        translator=pylons.translator._current_obj(),
        ungettext=pylons.i18n.ungettext,
        _=pylons.i18n._,
        N_=pylons.i18n.N_
    )
    
    # If the session was overriden to be None, don't populate the session
    # var
    if pylons.config['pylons.environ_config'].get('session', True):
        pylons_vars['session'] = pylons.session._current_obj()
    log.debug("Created render namespace with pylons vars: %s", pylons_vars)
    return pylons_vars


def cached_template(template_name, render_func, ns_options=(),
                    cache_key=None, cache_type=None, cache_expire=None,
                    **kwargs):
    """Cache and render a template
    
    Cache a template to the namespace ``template_name``, along with a
    specific key if provided.
    
    Basic Options
    
    ``template_name``
        Name of the template, which is used as the template namespace.
    ``render_func``
        Function used to generate the template should it no longer be
        valid or doesn't exist in the cache.
    ``ns_options``
        Tuple of strings, that should correspond to keys likely to be
        in the ``kwargs`` that should be used to construct the
        namespace used for the cache. For example, if the template
        language supports the 'fragment' option, the namespace should
        include it so that the cached copy for a template is not the
        same as the fragment version of it.
    
    Caching options (uses Beaker caching middleware)
    
    ``cache_key``
        Key to cache this copy of the template under.
    ``cache_type``
        Valid options are ``dbm``, ``file``, ``memory``, ``database``,
        or ``memcached``.
    ``cache_expire``
        Time in seconds to cache this template with this ``cache_key`` for.
        Or use 'never' to designate that the cache should never expire.
    
    The minimum key required to trigger caching is ``cache_expire='never'``
    which will cache the template forever seconds with no key.
    
    """
    # If one of them is not None then the user did set something
    if cache_key is not None or cache_expire is not None or cache_type \
        is not None:
        if not cache_type:
            cache_type = 'dbm'
        if not cache_key:
            cache_key = 'default'     
        if cache_expire == 'never':
            cache_expire = None
        namespace = template_name
        for name in ns_options:
            namespace += str(kwargs.get(name))
        cache = pylons.cache.get_cache(namespace)
        content = cache.get_value(cache_key, createfunc=render_func, 
            type=cache_type, expiretime=cache_expire)
        return content
    else:
        return render_func()


def render_mako(template_name, cache_key=None, cache_type=None, 
                cache_expire=None):
    """Render a template with Mako
    
    Accepts the cache options ``cache_key``, ``cache_type``, and
    ``cache_expire`` in addition to other keyword arguments that should
    be passed into Mako's ``Template.render`` function.
    
    """
    # First, get the globals
    globs = pylons_globals()
    
    # Grab a template reference
    template = globs['g'].mako_lookup.get_template(template_name)
    
    # Create a render callable for the cache function
    def render_template():
        return template.render(**globs)
    
    return cached_template(template_name, render_template, cache_key=cache_key, 
                           cache_type=cache_type, cache_expire=cache_expire,
                           ns_options=('fragment',))


def render_genshi(template_name, cache_key=None, cache_type=None, 
                  cache_expire=None, fragment=False, format='xhtml'):
    """Render a template with Genshi
    
    Accepts the cache options ``cache_key``, ``cache_type``, and
    ``cache_expire`` in addition to fragment and format which are
    passed to Genshi's render function.
    
    """
    # First, get the globals
    globs = pylons_globals()
    
    # Grab a template reference
    template = globs['g'].genshi_loader.load(template_name)
    
    # Create a render callable for the cache function
    def render_template():
        return template.generate(**globs).render()
    
    return cached_template(template_name, render_template, cache_key=cache_key, 
                           cache_type=cache_type, cache_expire=cache_expire,
                           ns_options=('fragment', 'format'), 
                           fragment=fragment, format=format)

class BuffetError(Exception):
    """Buffet Exception"""
    pass


class Buffet(object):
    """Buffet style plug-in template rendering
    
    Buffet implements template language plug-in support modeled highly on the
    `Buffet Project <http://projects.dowski.com/projects/buffet>`_ from which
    this class inherits its name.
    """
    def __init__(self, default_engine=None, template_root=None,
        default_options=None, **config):
        """Initialize the Buffet renderer, and optionally set a default
        engine/options"""
        if default_options is None:
            default_options = {}
        self.default_engine = default_engine
        self.template_root = template_root
        self.default_options = default_options
        self.engines = {}
        log.debug("Initialized Buffet object")
        if self.default_engine:
            self.prepare(default_engine, template_root, **config)
        
    def prepare(self, engine_name, template_root=None, alias=None, **config):
        """Prepare a template engine for use
        
        This method must be run before the `render <#render>`_ method is called
        so that the ``template_root`` and options can be set. Template engines
        can also be aliased if you wish to use multiplate configurations of the
        same template engines, or prefer a shorter name when rendering a
        template with the engine of your choice.
        """
        Engine = available_engines.get(engine_name, None)
        if not Engine:
            raise TemplateEngineMissing('Please install a plugin for '
                '"%s" to use its functionality' % engine_name)
        engine_name = alias or engine_name
        extra_vars_func = config.pop(engine_name + '.extra_vars_func', None)
        self.engines[engine_name] = \
            dict(engine=Engine(extra_vars_func=extra_vars_func,
                               options=config), 
                 root=template_root)
        log.debug("Adding %s template language for use with Buffet", 
                  engine_name)
        
    def render(self, engine_name=None, template_name=None,
               include_pylons_variables=True, namespace=None, 
               cache_key=None, cache_expire=None, cache_type=None, **options):
        """Render a template using a template engine plug-in
        
        To use templates it is expected that you will attach data to be used in
        the template to the ``c`` variable which is available in the controller
        and the template. 
        
        When porting code from other projects it is sometimes easier to use an
        exisitng dictionary which can be specified with ``namespace``.
        
        ``engine_name``
            The name of the template engine to use, which must be
            'prepared' first.
        ``template_name``
            Name of the template to render
        ``include_pylons_variables``
            If a custom namespace is specified this determines whether Pylons 
            variables are included in the namespace or not. Defaults to 
            ``True``.
        ``namespace``
            A custom dictionary of names and values to be substituted in the
            template.
        
        Caching options (uses Beaker caching middleware)
        
        ``cache_key``
            Key to cache this copy of the template under.
        ``cache_type``
            Valid options are ``dbm``, ``file``, ``memory``, or 
            ``ext:memcached``.
        ``cache_expire``
            Time in seconds to cache this template with this ``cache_key`` for.
            Or use 'never' to designate that the cache should never expire.
        
        The minimum key required to trigger caching is ``cache_expire='never'``
        which will cache the template forever seconds with no key.
        
        All other keyword options are passed directly to the template engine
        used.
        """
        if not engine_name and self.default_engine:
            engine_name = self.default_engine
        engine_config = self.engines.get(engine_name)
        
        if not engine_config:
            raise Exception("No engine with that name configured: %s" % \
                                engine_name)
        
        full_path = template_name
                
        if engine_name == 'pylonsmyghty':
            if namespace is None:
                namespace = {}
            # Reserved myghty keywords
            for key in ('output_encoding', 'encoding_errors', 
                        'disable_unicode'):
                if key in namespace:
                    options[key] = namespace.pop(key)

            if include_pylons_variables:
                namespace['_global_args'] = pylons_globals()
            else:
                namespace['_global_args'] = {}
            
            # If they passed in a variable thats listed in the global_args,
            # update the global args one instead of duplicating it
            interp = engine_config['engine'].interpreter
            for key in interp.global_args.keys() + \
                interp.init_params.get('allow_globals', []):
                if key in namespace:
                    namespace['_global_args'][key] = namespace.pop(key)
        else:
            if namespace is None:
                if not include_pylons_variables:
                    raise BuffetError('You must specify ``namespace`` when '
                                      '``include_pylons_variables`` is False')
                else:
                    namespace = pylons_globals()
            elif include_pylons_variables:
                globs = pylons_globals()
                globs.update(namespace)
                namespace = globs
            
            if not full_path.startswith(os.path.sep) and not \
                    engine_name.startswith('pylons') and not \
                    engine_name.startswith('mako') and \
                    engine_config['root'] is not None:
                full_path = os.path.join(engine_config['root'], template_name)
                full_path = full_path.replace(os.path.sep, '.').lstrip('.')
        
        # Don't pass format into the template engine if it's None
        if 'format' in options and options['format'] is None:
            del options['format']
        
        # If one of them is not None then the user did set something
        if cache_key is not None or cache_expire is not None or cache_type \
            is not None:
            if not cache_type:
                cache_type = 'dbm'
            if not cache_key:
                cache_key = 'default'     
            if cache_expire == 'never':
                cache_expire = None
            def content():
                log.debug("Cached render running for %s", full_path)
                return engine_config['engine'].render(namespace, 
                    template=full_path, **options)
            tfile = full_path
            if options.get('fragment', False):
                tfile += '_frag'
            if options.get('format', False):
                tfile += options['format']
            log.debug("Using render cache for %s", full_path)
            mycache = pylons.cache.get_cache(tfile)
            content = mycache.get_value(cache_key, createfunc=content, 
                type=cache_type, expiretime=cache_expire)
            return content
        
        log.debug("Rendering template %s with engine %s", full_path, 
                  engine_name)
        return engine_config['engine'].render(namespace, template=full_path, 
            **options)


class TemplateEngineMissing(Exception):
    """Exception to toss when an engine is missing"""
    pass


class MyghtyTemplatePlugin(object):
    """Myghty Template Plugin
    
    This Myghty Template Plugin varies from the official BuffetMyghty in that 
    it will properly populate all the default Myghty variables needed and 
    render fragments.
    """
    extension = "myt"

    def __init__(self, extra_vars_func=None, options=None):
        """Initialize Myghty template engine"""
        if options is None:
            options = {}
        myt_opts = {}
        for k, v in options.iteritems():
            if k.startswith('myghty.'):
                myt_opts[k[7:]] = v
        import myghty.interp
        self.extra_vars = extra_vars_func
        self.interpreter = myghty.interp.Interpreter(**myt_opts)
    
    def load_template(self, template_path):
        """Unused method for TG plug-in API compatibility"""
        pass

    def render(self, info, format="html", fragment=False, template=None,
               output_encoding=None, encoding_errors=None,
               disable_unicode=None):
        """Render the template indicated with info as the namespace and globals
        from the ``info['_global_args']`` key."""
        buf = StringIO()
        global_args = info.pop('_global_args')
        if self.extra_vars:
            global_args.update(self.extra_vars())
        optional_args = {}
        if fragment:
            optional_args['disable_wrapping'] = True
        if output_encoding:
            optional_args['output_encoding'] = output_encoding
        if encoding_errors:
            optional_args['encoding_errors'] = encoding_errors
        if disable_unicode:
            optional_args['disable_unicode'] = disable_unicode
        self.interpreter.execute(template, request_args=info,
                                 global_args=global_args, out_buffer=buf,
                                 **optional_args)
        return buf.getvalue()


available_engines = {}


for entry_point in \
        pkg_resources.iter_entry_points('python.templating.engines'):
    try:
        Engine = entry_point.load()
        available_engines[entry_point.name] = Engine
    except:
        import sys
        from pkg_resources import DistributionNotFound
        # Warn when there's a problem loading a Buffet plugin unless it's
        # pylonsmyghty reporting there's no Myghty installed
        if not isinstance(sys.exc_info()[1], DistributionNotFound) or \
                entry_point.name != 'pylonsmyghty':
            import traceback
            tb = StringIO()
            traceback.print_exc(file=tb)
            warnings.warn("Unable to load template engine entry point: '%s': "
                          "%s" % (entry_point, tb.getvalue()), RuntimeWarning,
                          2)


def render(*args, **kargs):
    """Render a template and return it as a string (possibly Unicode)
    
    Optionally takes 3 keyword arguments to use caching supplied by Buffet.
    
    Examples:
        
    .. code-block:: Python

        content = render('/my/template.mako')
        print content
        content = render('/my/template2.myt', fragment=True)
    
    .. admonition:: Note
        
        Not all template languages support the concept of a fragment. In those
        template languages that do support the fragment option, this usually 
        implies that the template will be rendered without extending or 
        inheriting any site skin.
    """
    fragment = kargs.pop('fragment', False)
    format = kargs.pop('format', None)
    args = list(args)
    template = args.pop()
    cache_args = dict(cache_expire=kargs.pop('cache_expire', None), 
                       cache_type=kargs.pop('cache_type', None),
                       cache_key=kargs.pop('cache_key', None))
    log.debug("Render called with %s args and %s keyword args", args, kargs)
    if args: 
        engine = args.pop()
        return pylons.buffet.render(engine, template, fragment=fragment,
                                    format=format, namespace=kargs, 
                                    **cache_args)
    return pylons.buffet.render(template_name=template, fragment=fragment,
                                format=format, namespace=kargs, **cache_args)


def render_response(*args, **kargs):
    """Returns the rendered response within a Response object
    
    See ``render`` for information on rendering.
    
    Example:
    
    .. code-block:: Python
        
        def view(self):
            return render_response('/my/template.mako')
    """
    warnings.warn(pylons.legacy.render_response_warning, DeprecationWarning, 2)

    response = pylons.response._current_obj()
    response.content = render(*args, **kargs)
    output_encoding = kargs.get('output_encoding')
    encoding_errors = kargs.get('encoding_errors')
    if output_encoding:
        response.headers['Content-Type'] = '%s; charset=%s' % \
            (pylons.response.default_content_type, output_encoding)
    if encoding_errors:
        response.encoding_errors = encoding_errors
    return ''
render_response.__doc__ = 'Deprecated: %s.\n\n%s' % \
    (pylons.legacy.render_response_warning, render_response.__doc__)
