/*
 * Edge® 540 / 540 Solar Personality
 * Begin Common
 */

/* System Definitions */

device_info {
    hasTouchScreen: false;
    hasNightMode: true;
    hasEnhancedReadabilityMode: false;
    hasGpu: false;
    screenWidth: 246;
    screenHeight: 322;
    screenShape: Toybox.System.SCREEN_SHAPE_RECTANGLE;
}

system_size__screen {
    width: 246;
    height: 322;
}

system_size__launch_icon {
    scaleX: 35;
    scaleY: 35;
    scaleRelativeTo: screen;
}

system_color_light__background {
    color: #FFFFFF;
    background: #FFFFFF;
}

system_color_dark__background {
    color: #000000;
    background: #000000;
}

system_color_light__text {
    color: #000000;
    background: Graphics.COLOR_TRANSPARENT;
}

system_color_dark__text {
    color: #FFFFFF;
    background: Graphics.COLOR_TRANSPARENT;
}

/* Subwindow Definitions */

system_loc__subwindow {
    exclude: true;
}

system_size__subwindow {
    exclude: true;
}

/* Menu Definitions */

system_size__menu_header {
    height: 0;
}

system_size__menu_item {
    width: 246;
    height: 70;
}

system_size__menu_icon {
    scaleX: 40;
    scaleY: 40;
    scaleRelativeTo: screen;
}

/* Activity Definitions */

activity_color_light__background {
    color: #FFFFFF;
    background: #FFFFFF;
}

activity_color_dark__background {
    color: #000000;
    background: #000000;
}

activity_color_light__text {
    color: #000000;
    background: Graphics.COLOR_TRANSPARENT;
}

activity_color_dark__text {
    color: #FFFFFF;
    background: Graphics.COLOR_TRANSPARENT;
}

/* Prompt Definitions */

prompt_color_light__background {
    background: #FFFFFF;
    color: #FFFFFF;
}

prompt_color_dark__background {
    background: #000000;
    color: #000000;
}

prompt_color_light__title {
    color: #000000;
    background: Graphics.COLOR_TRANSPARENT;
}

prompt_color_dark__title {
    color: #FFFFFF;
    background: Graphics.COLOR_TRANSPARENT;
}

prompt_color_light__body {
    color: #000000;
    background: Graphics.COLOR_TRANSPARENT;
}

prompt_color_dark__body {
    color: #FFFFFF;
    background: Graphics.COLOR_TRANSPARENT;
}

prompt_font__title {
    exclude: true;
}

prompt_font_enhanced_readability__title {
    exclude: true;
}

prompt_font__body_no_title {
    font: Graphics.FONT_MEDIUM;
    justification: Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER;
}

prompt_font_enhanced_readability__body_no_title {
    exclude: true;
}

prompt_font__body_with_title {
    font: Graphics.FONT_MEDIUM;
    justification: Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER;
}

prompt_font_enhanced_readability__body_with_title {
    exclude: true;
}

prompt_loc__title_icon {
    x: 123;
    y: 44;
    horizontalJustification: center;
    verticalJustification: center;
}

prompt_loc__title {
    exclude: true;
}

prompt_loc__body_with_title {
    x: 8;
    y: 93;
}

prompt_loc__body_no_title {
    x: 8;
    y: 14;
}

prompt_size__title {
    exclude: true;
}

prompt_size__body_no_title {
    width: 230;
    height: 225;
}

prompt_size__body_with_title {
    width: 230;
    height: 135;
}

prompt_size__title_icon {
    scaleX: 55;
    scaleY: 55;
    scaleRelativeTo: screen;
}

/* Action Menus */

system_icon_light__hint_action_menu {
    exclude: true;
}

system_icon_dark__hint_action_menu {
    exclude: true;
}

system_icon_positive__hint_action_menu {
    exclude: true;
}

system_icon_destructive__hint_action_menu {
    exclude: true;
}

system_loc__hint_action_menu {
    exclude: true;
}

system_input__action_menu {
    button: WatchUi.KEY_MENU;
}

/* Confirmations */

confirmation_color_light__body {
    color: #000000;
    background: Graphics.COLOR_TRANSPARENT;
}

confirmation_color_dark__body {
    color: #FFFFFF;
    background: Graphics.COLOR_TRANSPARENT;
}

confirmation_icon__hint_confirm {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon__hint_reject {
    filename: "confirmation_icon__hint_x.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon__hint_delete {
    filename: "confirmation_icon__hint_back.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon__hint_keep {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: false;
}

confirmation_icon_light__hint_confirm {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_light__hint_reject {
    filename: "confirmation_icon__hint_x.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_light__hint_delete {
    filename: "confirmation_icon__hint_back.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_light__hint_keep {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: false;
}

confirmation_icon_dark__hint_confirm {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_dark__hint_reject {
    filename: "confirmation_icon__hint_x.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_dark__hint_delete {
    filename: "confirmation_icon__hint_back.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

confirmation_icon_dark__hint_keep {
    filename: "confirmation_icon__hint_completion.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: false;
}

confirmation_font__body {
    font: Graphics.FONT_MEDIUM;
    justification: Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER;
}

confirmation_font_enhanced_readability__body {
    exclude: true;
}

confirmation_loc__hint_confirm {
    x: 195;
    y: 30;
    horizontalJustification: center;
    verticalJustification: center;
}

confirmation_loc__hint_reject {
    x: 214;
    y: 249;
    horizontalJustification: center;
    verticalJustification: center;
}

confirmation_loc__hint_delete {
    x: 195;
    y: 249;
    horizontalJustification: center;
    verticalJustification: center;
}

confirmation_loc__hint_keep {
    x: 195;
    y: 30;
    horizontalJustification: center;
    verticalJustification: center;
}

confirmation_loc__body {
    x: 1;
    y: 23;
}

confirmation_size__body {
    width: 246;
    height: 236;
}

confirmation_input__confirm {
    button: WatchUi.KEY_ENTER;
}

confirmation_input__reject {
    button: WatchUi.KEY_ESC;
}

confirmation_input__delete {
    button: WatchUi.KEY_ESC;
}

confirmation_input__keep {
    button: WatchUi.KEY_ENTER;
}

/* Toasts */

system_size__toast_icon {
    scaleX: 32;
    scaleY: 32;
    scaleRelativeTo: screen;
}

/* System Assets */

system_icon_light__about {
    filename: "system_icon_light__about.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__cancel {
    filename: "system_icon_light__cancel.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__check {
    filename: "system_icon_light__check.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__discard {
    filename: "system_icon_light__discard.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__question {
    filename: "system_icon_light__question.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__revert {
    filename: "system_icon_light__revert.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__save {
    filename: "system_icon_light__save.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__search {
    filename: "system_icon_light__search.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_light__warning {
    filename: "system_icon_light__warning.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__about {
    filename: "system_icon_dark__about.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__cancel {
    filename: "system_icon_dark__cancel.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__check {
    filename: "system_icon_dark__check.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__discard {
    filename: "system_icon_dark__discard.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__question {
    filename: "system_icon_dark__question.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__revert {
    filename: "system_icon_dark__revert.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__save {
    filename: "system_icon_dark__save.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__search {
    filename: "system_icon_dark__search.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_dark__warning {
    filename: "system_icon_dark__warning.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_positive__check {
    filename: "system_icon_positive__check.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_destructive__cancel {
    filename: "system_icon_destructive__cancel.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_destructive__discard {
    filename: "system_icon_destructive__discard.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

system_icon_destructive__warning {
    filename: "system_icon_destructive__warning.svg";
    dithering: "none";
    automaticPalette: true;
    disableTransparency: true;
}

/* Button Hints */

system_icon_light__hint_button_left_top {
    exclude: true;
}

system_icon_light__hint_button_left_middle {
    exclude: true;
}

system_icon_light__hint_button_left_bottom {
    exclude: true;
}

system_icon_light__hint_button_right_top {
    exclude: true;
}

system_icon_light__hint_button_right_middle {
    exclude: true;
}

system_icon_light__hint_button_right_bottom {
    exclude: true;
}

system_icon_dark__hint_button_left_top {
    exclude: true;
}

system_icon_dark__hint_button_left_middle {
    exclude: true;
}

system_icon_dark__hint_button_left_bottom {
    exclude: true;
}

system_icon_dark__hint_button_right_top {
    exclude: true;
}

system_icon_dark__hint_button_right_middle {
    exclude: true;
}

system_icon_dark__hint_button_right_bottom {
    exclude: true;
}

system_icon_positive__hint_button_left_top {
    exclude: true;
}

system_icon_positive__hint_button_left_middle {
    exclude: true;
}

system_icon_positive__hint_button_left_bottom {
    exclude: true;
}

system_icon_positive__hint_button_right_top {
    exclude: true;
}

system_icon_positive__hint_button_right_middle {
    exclude: true;
}

system_icon_positive__hint_button_right_bottom {
    exclude: true;
}

system_icon_destructive__hint_button_left_top {
    exclude: true;
}

system_icon_destructive__hint_button_left_middle {
    exclude: true;
}

system_icon_destructive__hint_button_left_bottom {
    exclude: true;
}

system_icon_destructive__hint_button_right_top {
    exclude: true;
}

system_icon_destructive__hint_button_right_middle {
    exclude: true;
}

system_icon_destructive__hint_button_right_bottom {
    exclude: true;
}

system_loc__hint_button_left_top {
    exclude: true;
}

system_loc__hint_button_left_middle {
    exclude: true;
}

system_loc__hint_button_left_bottom {
    exclude: true;
}

system_loc__hint_button_right_top {
    exclude: true;
}

system_loc__hint_button_right_middle {
    exclude: true;
}

system_loc__hint_button_right_bottom {
    exclude: true;
}
