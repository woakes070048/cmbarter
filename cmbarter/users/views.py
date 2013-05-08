## The author disclaims copyright to this source code.  In place of
## a legal notice, here is a poem:
##
##   "Metaphysics"
##   
##   Matter: is the music 
##   of the space.
##   Music: is the matter
##   of the soul.
##   
##   Soul: is the space
##   of God.
##   Space: is the soul
##   of logic.
##   
##   Logic: is the god
##   of the mind.
##   God: is the logic
##   of bliss.
##   
##   Bliss: is a mind
##   of music.
##   Mind: is the bliss
##   of the matter.
##   
######################################################################
## This file contains django view-functions implementing the singing
## up, logging in, and completing users' profiles.
##
from __future__ import with_statement
import random
import re, os
from urllib import urlencode
from django.conf import settings
from django.shortcuts import render_to_response
from django.core.urlresolvers import reverse
from django.core.context_processors import csrf
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotAllowed, HttpResponseForbidden, Http404
from django.utils.translation import get_language
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_protect
from cmbarter.users import forms
from cmbarter.users.decorators import logged_in, has_profile, max_age
from cmbarter.modules import curiousorm, utils, captcha, keygen, limiter
from pytz import common_timezones


db = curiousorm.Database(settings.CMBARTER_DSN)

TRADER_ID_STRING = re.compile('^\d{1,9}$')
SSI_HOST = re.compile('<!--\s*#echo\s*var="HTTP_HOST"\s*-->')


search_limiter = limiter.Limiter(
    os.path.join(settings.CMBARTER_SESSION_DIR, "cmbarter_search_limiter"),
    settings.CMBARTER_SEARCH_MAX_PER_SECOND,
    settings.CMBARTER_SEARCH_MAX_BURST)


@csrf_protect
@curiousorm.retry_transient_errors
def login(request, tmpl='login.html'):
    if request.method == 'POST':
        form = forms.LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password_salt = db.get_password_salt(username)
            password_hash = utils.calc_crypt_hash(password_salt + form.cleaned_data['password'])

            authentication = db.login_trader(username, password_hash)

            if settings.CMBARTER_SHOW_CAPTCHA_ON_REPETITIVE_LOGIN and authentication['needs_captcha']:
                # Challenge the user with a captcha.
                request.session['auth_username'] = username
                request.session['auth_is_valid'] = authentication['is_valid']
                request.session['auth_trader_id'] = authentication['trader_id']
                return HttpResponseRedirect(reverse(login_captcha))

            elif authentication['is_valid']:
                # Log the user in and redirect him to his start-page.
                trader_id = request.session['trader_id'] = authentication['trader_id']
                show = TRADER_ID_STRING.match(request.GET.get('show', ''))
                if show:
                    return HttpResponseRedirect(reverse(
                        'products-partner-pricelist', args=[trader_id, int(show.group())]))
                else:
                    return HttpResponseRedirect(reverse(
                        'profiles-check-email', args=[trader_id]))
            else:
                form.incorrect_login = True                
    else:
        prefill_username = request.GET.get('username', '')
        form = forms.LoginForm(initial={'username': prefill_username })
        form.incorrect_login = bool(prefill_username)

    # Render everything adding CSRF protection.
    c = {'settings': settings, 'form': form }
    c.update(csrf(request))
    return render_to_response(tmpl, c)        


@csrf_protect
@curiousorm.retry_transient_errors
def login_captcha(request, tmpl='login_captcha.html'):
    captcha_error = None
    
    if request.method == 'POST':
        captcha_response = captcha.submit(
            request.POST.get('recaptcha_challenge_field'),
            request.POST.get('recaptcha_response_field'),
            settings.RECAPTCHA_PIVATE_KEY,
            request.META['REMOTE_ADDR'])
        captcha_error = captcha_response.error_code

        if captcha_response.is_valid:
            if request.session.get('auth_is_valid'):
                # a successful login
                trader_id = request.session['auth_trader_id']
                del request.session['auth_username']
                del request.session['auth_is_valid']
                del request.session['auth_trader_id']
                db.report_login_captcha_success(trader_id)
                request.session['trader_id'] = trader_id
                return HttpResponseRedirect(reverse(
                    'profiles-check-email', args=[trader_id]))

            else:
                # an incorrect login
                username = request.session.get('auth_username', '').encode('UTF-8')
                return HttpResponseRedirect("%s?%s" % (
                    reverse(login),
                    urlencode({'username': username })))
                
    # Render everything adding CSRF protection.
    c = {'settings': settings, 'captcha_error': captcha_error }
    c.update(csrf(request))
    return render_to_response(tmpl, c)        


@curiousorm.retry_transient_errors
def verify_email(request, trader_id_str, verification_code, tmpl='verified_email.html'):
    trader_id = int(trader_id_str)

    if db.verify_email(trader_id, verification_code):
        # Render the template (we do not need CSRF protection here).
        c = {'settings': settings }
        return render_to_response(tmpl, c)
    
    else:
        raise Http404


@curiousorm.retry_transient_errors
def cancel_email(request, trader_id_str, cancellation_code, tmpl='cancel_email.html'):

    # We should make sure that the user has got a verified email, and
    # he has provided the correct cancellation code.
    email = db.get_verified_email(int(trader_id_str))

    if email and email['email_cancellation_code']==cancellation_code:

        if request.method == 'POST':
            form = forms.CancelEmailForm(request.POST)
            if form.is_valid():
                db.cancel_email(email['trader_id'], email['email_cancellation_code'])

                return HttpResponseRedirect("%s?%s" % (
                    reverse(report_cancel_email_success),
                    urlencode({'email': email['email'] })))
        else:
            form = forms.CancelEmailForm()

        # Render the template (we do not need CSRF protection here).
        c = {'settings': settings, 'form': form, 'email': email['email'] }
        return render_to_response(tmpl, c)

    else:
        raise Http404


def report_cancel_email_success(request, tmpl='cancel_email_success.html'):

    # Render the template (we do not need CSRF protection here).    
    c = {'settings': settings, 'email': request.GET.get('email') }
    return render_to_response(tmpl, c)


@logged_in
def logout(request, trader_id):
    if request.method == 'POST':
        request.session.flush()
        return HttpResponseRedirect(reverse(login))

    return HttpResponseNotAllowed(['POST'])


def show_about(request, tmpl='about.html'):

    # Render the template (we do not need CSRF protection here).    
    c = {'settings': settings }
    return render_to_response(tmpl, c)        


def show_trader(request, trader_id_str):
    trader_id = int(trader_id_str)

    if 'trader_id' in request.session:
        return HttpResponseRedirect(reverse(
            'products-partner-pricelist',
            args=[request.session['trader_id'], trader_id]))
    else:
        return HttpResponseRedirect("%s?show=%s" % (
            reverse(login), trader_id))


def search(request, trader_id_str, tmpl='search.html'):
    # Make sure we do not propagate DoS attacks to the database:
    if not search_limiter.allow_request():
        return HttpResponseForbidden()

    trader_id = int(trader_id_str)

    trader = db.get_profile(trader_id)

    if trader and trader['advertise_trusted_partners']==True:
        # We are ALLOWED to show this info -- Parse GET-ed parameters and query the database.
        query = request.GET.get('q', '')[:70]
        try:
            current_page = max(int(request.GET.get('page', '')[:3]), 1)
        except ValueError:
            current_page = 1
        number_of_items, number_of_pages, number_of_items_per_page = db.get_trust_match_count(trader_id, query)
        visible_items = db.get_trust_match_list(trader_id, query, current_page - 1)

        # Render everything (we do not need CSRF protection here).
        a = (current_page - 1) * number_of_items_per_page + 1
        b = 5
        c =  {'settings': settings,
              'query': query, 'current_page': current_page,
              'number_of_items': number_of_items,
              'number_of_pages': number_of_pages,
              'number_of_items_per_page': number_of_items_per_page,
              'visible_items': visible_items,
              'first_visible_item_seqnum': a,
              'last_visible_item_seqnum': a - 1 + (len(visible_items) if len(visible_items) > 0 else number_of_items_per_page),
              'visible_pages': range(max(1, current_page - b + 1), 1 + min(number_of_pages, current_page + b)) }
        return render_to_response(tmpl, c)

    raise Http404


@csrf_protect
@curiousorm.retry_transient_errors
def signup(request, tmpl='signup.html'):
    captcha_error = None
    
    if request.method == 'POST':
        if settings.CMBARTER_SHOW_CAPTCHA_ON_SIGNUP:
            captcha_response = captcha.submit(
                request.POST.get('recaptcha_challenge_field'),
                request.POST.get('recaptcha_response_field'),
                settings.RECAPTCHA_PIVATE_KEY,
                request.META['REMOTE_ADDR'])
            captcha_error = captcha_response.error_code
            captcha_passed = captcha_response.is_valid
        else:    
            captcha_passed = True

        form = forms.SignupForm(request.POST)
        if captcha_passed and form.is_valid():
            username = form.cleaned_data['username']            
            password_salt = utils.generate_password_salt()
            password_hash = utils.calc_crypt_hash(password_salt + form.cleaned_data['password'])
            registration_key = keygen.Keygen(settings.SECRET_KEY, settings.CMBARTER_REGISTRATION_KEY_PREFIX).validate(form.cleaned_data['registration_key']) if settings.CMBARTER_REGISTRATION_KEY_IS_REQUIRED else None
            
            while 1:
                # Generate a new trader ID and try to register it.
                trader_id = utils.vh_compute(random.randrange(1, 100000000))
                error = db.insert_trader(trader_id, username, get_language(), password_hash, password_salt, registration_key)
                
                if 3==error:
                    # The registration key is invalid.
                    form.invalid_regkey = True
                    break

                elif 2==error:                    
                    # The username is taken.                    
                    form.username_taken = True
                    break
                
                elif 1==error:
                    # Probably the ID is taken -- keep trying.
                    continue  

                else:
                    # Successfunl registration -- log the user in and
                    # redirect him to copmlete his profile.
                    request.session['trader_id'] = trader_id
                    return HttpResponseRedirect(reverse(
                        create_profile, args=[trader_id]))
    else:
        form = forms.SignupForm()

    # Render everything adding CSRF protection.
    c = {'settings': settings, 'form': form, 'captcha_error': captcha_error }
    c.update(csrf(request))
    return render_to_response(tmpl, c)        


@logged_in
@curiousorm.retry_transient_errors
def create_profile(request, trader_id, tmpl='create_profile.html'):
    if request.method == 'POST':
        form = forms.CreateProfileForm(request.POST)
        if form.is_valid():
            db.insert_profile(trader_id,
                form.cleaned_data['full_name'],
                form.cleaned_data['summary'],
                form.cleaned_data['country'],
                form.cleaned_data['postal_code'],
                form.cleaned_data['address'],
                form.cleaned_data['email'],
                form.cleaned_data['phone'],
                form.cleaned_data['fax'],
                form.cleaned_data['www'],
                form.cleaned_data['time_zone'])
            return HttpResponseRedirect(reverse(
                report_signup_success, args=[trader_id]))
    else:
        form = forms.CreateProfileForm(
            initial={'time_zone': settings.CMBARTER_DEFAULT_USERS_TIME_ZONE })

    # Fetches all known time zones in the form's time-zone-select-box.
    form.fields['time_zone'].widget.choices = [
        ('', _('Select your time zone'))] + [
        (tz, tz) for tz in common_timezones]

    # Render everything adding CSRF protection.        
    c = {'settings': settings, 'trader_id': trader_id, 'form': form }
    c.update(csrf(request))
    return render_to_response(tmpl, c)        


@logged_in
def report_signup_success(request, trader_id, tmpl='signup_success.html'):

    # Render everything adding CSRF protection.    
    c = {'settings': settings, 'user': {'trader_id': trader_id, 'offers_are_enabled': True } }
    c.update(csrf(request))
    return render_to_response(tmpl, c)        


def set_language(request, lang):
    r = HttpResponseRedirect(reverse(show_about))
    r.set_cookie(
        key=settings.LANGUAGE_COOKIE_NAME,
        value=lang,
        max_age=60*60*24*365*10)
    return r


@max_age(1209600)
def show_manual(request, lang):
    fullpath = os.path.join(settings.CMBARTER_DEV_DOC_ROOT, lang, 'index.shtml')
    if os.path.isdir(fullpath) or not os.path.exists(fullpath):
        raise Http404

    with open(fullpath, 'rb') as manual:
        s = manual.read()
        s = SSI_HOST.sub(settings.CMBARTER_HOST.encode('utf-8'), s)

    return HttpResponse(s, content_type='text/html; charset=utf-8')


def csrf_abort(request, reason=''):
    return render_to_response('csrf_abort.html')